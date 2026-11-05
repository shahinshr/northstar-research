"""Versioned REST API (spec §16). Browser reads canonical values + provenance, never raw provider payloads."""
from __future__ import annotations

import csv
import io
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..analytics.funds import overlap as fund_overlap
from ..analytics.fx import FXTable
from ..analytics.market import bars_frame
from ..config import get_settings
from ..db import (Alert, AnalysisResult, DatasetSnapshot, Instrument, Job, Note, Scenario, SessionLocal, WatchlistItem,
                  get_setting, latest_completed_run, latest_snapshot, put_setting)
from ..providers.base import Provider
from ..providers.canonical import normalise_quote_currency
from ..providers.registry import providers
from ..services import jobs
from ..services.analysis import SHORTLIST_THRESHOLD, clean_json, make_row
from ..services.ingest import MACRO, datasets_for, ensure_instrument

router = APIRouter(prefix="/v1")
_row_cache: dict[tuple[str, str], list[dict]] = {}


def _job_dict(j: Job | None) -> dict | None:
    if j is None:
        return None
    p = dict(j.progress or {})
    p.pop("items", None)
    return {"job_id": j.id, "kind": j.kind, "status": j.status, "progress": p, "message": j.message,
            "created_at": j.created_at.isoformat(), "updated_at": j.updated_at.isoformat(), "run_id": j.run_id}


def _rows(s, run_id: str, asset: str | None = None) -> list[dict]:
    """Slim rows for a run (cached per run — published snapshots are immutable)."""
    key = (run_id, asset or "*")
    if key not in _row_cache:
        q = select(AnalysisResult.row).where(AnalysisResult.run_id == run_id)
        if asset:
            q = q.where(AnalysisResult.asset_class == asset)
        rows = list(s.scalars(q))
        if not rows or any(r is None for r in rows):  # legacy runs stored before slim rows existed
            rows = [make_row(r.payload) for r in s.scalars(select(AnalysisResult).where(AnalysisResult.run_id == run_id))]
            if asset:
                rows = [r for r in rows if r.get("asset_class", "stock") == asset]
        if len(_row_cache) > 20:
            _row_cache.clear()
        _row_cache[key] = rows
    return _row_cache[key]


def _row(p: dict, watch: set[str]) -> dict:
    r = make_row(p)
    r["watch"] = p["id"] in watch
    return r


# ------------------------------------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard(asset: Literal["stock", "fund"] = "stock"):
    with SessionLocal() as s:
        run = latest_completed_run(s)
        prev = latest_completed_run(s, offset=1)
        watch = {w.instrument_id for w in s.scalars(select(WatchlistItem))}
        active = s.scalars(select(Job).where(Job.status.in_(jobs.ACTIVE)).order_by(Job.created_at.desc())).first()
        last_job = s.scalars(select(Job).order_by(Job.created_at.desc()).limit(1)).first()
        if run is None:
            return {"run": None, "rows": [], "changes": None, "active_job": _job_dict(active or last_job),
                    "state": "first_run_backfill"}
        rows = [dict(r, watch=r["id"] in watch) for r in _rows(s, run.id, asset)]
        rows.sort(key=lambda r: (r["core"]["score"] is None, -(r["core"]["score"] or 0), r["symbol"]))
        changes = {"entrants": [], "exits": [], "movers": [], "compared_to": None}
        if prev and asset == "stock":
            old = {r["id"]: r for r in _rows(s, prev.id, "stock")}
            changes["compared_to"] = {"run_id": prev.id, "created_at": prev.created_at.isoformat()}

            def sc(r):
                return ((r or {}).get("core") or {}).get("score")
            for r in rows:
                o = old.get(r["id"])
                a, b = sc(r), sc(o)
                if a is not None and a >= SHORTLIST_THRESHOLD and not (b is not None and b >= SHORTLIST_THRESHOLD):
                    changes["entrants"].append({"id": r["id"], "symbol": r["symbol"], "score": a})
                if b is not None and b >= SHORTLIST_THRESHOLD and not (a is not None and a >= SHORTLIST_THRESHOLD):
                    changes["exits"].append({"id": r["id"], "symbol": r["symbol"], "score": a})
                if a is not None and b is not None and abs(a - b) >= 5:
                    changes["movers"].append({"id": r["id"], "symbol": r["symbol"], "from": b, "to": a, "delta": round(a - b, 1)})
            changes["movers"].sort(key=lambda x: -abs(x["delta"]))
            changes["movers"] = changes["movers"][:15]
        stale = sum(1 for r in rows if ((r.get("confidence") or {}).get("freshness") or {}).get("price_stale"))
        unread = s.query(Alert).filter(Alert.read.is_(False)).count()
        return clean_json({
            "run": {"id": run.id, "created_at": run.created_at.isoformat(), "manifest": run.manifest,
                    "model_versions": run.model_versions},
            "rows": rows, "changes": changes, "active_job": _job_dict(active), "unread_alerts": unread,
            "health": {"stale_prices": stale, "failed": sum(1 for r in rows if r.get("status") != "ok"),
                       "providers_configured": [p.id for p in providers().values() if p.configured]},
            "presentation_currency": get_settings().presentation_currency,
            "state": "ok" if rows else "no_candidates",
        })


# ------------------------------------------------------------------------------------------------ instruments
@router.get("/instruments")
def list_instruments(q: str | None = None, include_benchmarks: bool = False, limit: int = Query(50, le=2000)):
    with SessionLocal() as s:
        rows = s.scalars(select(Instrument).where(Instrument.active.is_(True))).all()
        out = []
        for i in rows:
            if i.asset_type == "index" and not include_benchmarks:
                continue
            if q and q.lower() not in f"{i.symbol} {i.name or ''} {i.isin or ''} {i.exchange or ''}".lower():
                continue
            out.append({"id": i.id, "symbol": i.symbol, "name": i.name, "exchange": i.exchange, "region": i.region,
                        "currency": i.currency, "asset_type": i.asset_type, "sector": i.sector, "isin": i.isin})
        out.sort(key=lambda r: (not (q and r["symbol"].lower().startswith(q.lower())), r["symbol"]))
        return out[:limit]


class AddInstrument(BaseModel):
    symbol: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9.\-^=]+$")


@router.post("/instruments", status_code=202)
def add_instrument(body: AddInstrument):
    with SessionLocal() as s:
        inst = ensure_instrument(s, body.symbol, tags={"user_added": True})
        inst.active = True
        s.commit()
        iid = inst.id
    job, dedup = jobs.submit("refresh_analysis", [iid], None, "force")
    return {"instrument_id": iid, **_job_dict(job), "deduplicated": dedup}


@router.delete("/instruments/{iid}")
def deactivate_instrument(iid: str):
    with SessionLocal() as s:
        inst = s.get(Instrument, iid)
        if not inst:
            raise HTTPException(404, "Instrument not found")
        inst.active = False  # history is preserved (spec §4 step 1)
        s.commit()
    return {"ok": True}


def _analysis_payload(s, iid: str) -> tuple[dict, object]:
    run = latest_completed_run(s)
    if run is None:
        raise HTTPException(404, "No completed analysis yet")
    r = s.scalars(select(AnalysisResult).where(AnalysisResult.run_id == run.id,
                                               AnalysisResult.instrument_id == iid)).first()
    if r is None:
        raise HTTPException(404, "Instrument not in the latest snapshot (refresh pending?)")
    return r.payload, run


@router.get("/instruments/{iid}/analysis")
def analysis(iid: str):
    with SessionLocal() as s:
        p, run = _analysis_payload(s, iid)
        p = dict(p)
        p["watch"] = s.get(WatchlistItem, iid) is not None
        p["run"] = {"id": run.id, "created_at": run.created_at.isoformat(), "manifest": run.manifest}
        return clean_json(p)


@router.get("/instruments/{iid}/prices")
def prices(iid: str, range: Literal["1y", "3y", "5y", "10y"] = "5y"):
    with SessionLocal() as s:
        inst = s.get(Instrument, iid)
        snap = latest_snapshot(s, iid, "prices")
        if not inst or not snap:
            raise HTTPException(404, "No prices")
        prof = latest_snapshot(s, iid, "profile")
        ccy, scale = normalise_quote_currency((prof.payload if prof else {}).get("quote_currency")
                                              or snap.payload.get("quote_currency") or inst.currency)
        df, _ = bars_frame(snap.payload, inst.region or "US", scale)
        years = int(range[:-1])
        df = df[df.index >= df.index[-1] - pd.Timedelta(days=365 * years)]
        if years >= 5:
            df = df.resample("W-FRI").last().dropna(subset=["c"])
        dd = df["ac"] / df["ac"].cummax() - 1
        return clean_json({"currency": ccy, "provider": snap.provider, "adjustment": snap.payload.get("adjustment"),
                           "bars": [{"d": d.date().isoformat(), "c": r.c, "ac": r.ac, "v": r.v, "dd": dd.loc[d]}
                                    for d, r in df.iterrows()]})


@router.get("/instruments/{iid}/financials")
def financials(iid: str):
    with SessionLocal() as s:
        snap = latest_snapshot(s, iid, "financials")
        if not snap:
            raise HTTPException(404, "No financial statements")
        return clean_json({"provider": snap.provider, "route": snap.route, "fetched_at": snap.fetched_at.isoformat(),
                           "status": snap.status, **snap.payload})


# ------------------------------------------------------------------------------------------------ comparison
class CompareReq(BaseModel):
    instrument_ids: list[str] = Field(min_length=2, max_length=8)
    currency: Literal["CAD", "USD", "GBP"] = "CAD"
    window: Literal["1y", "3y", "5y"] = "3y"


@router.post("/comparisons")
def compare(req: CompareReq):
    with SessionLocal() as s:
        fx_snap = latest_snapshot(s, MACRO, "fx")
        fx = FXTable(fx_snap.payload if fx_snap else None)
        items, series = [], {}
        for iid in req.instrument_ids:
            p, run = _analysis_payload(s, iid)
            items.append(_row(p, set()) | {"scores": p.get("scores"), "financial_currency": p.get("financial_currency"),
                                           "metrics": p.get("metrics") or {}})  # full set, not the table subset
            inst = s.get(Instrument, iid)
            snap = latest_snapshot(s, iid, "prices")
            if not snap:
                continue
            ccy, scale = normalise_quote_currency(p.get("quote_currency_raw"))
            df, _ = bars_frame(snap.payload, inst.region or "US", scale)
            conv = fx.series_rate(ccy, req.currency, df.index)
            series[p["symbol"]] = {"local": df["ac"], "conv": df["ac"] * conv if conv is not None else None}
        years = int(req.window[:-1])
        # common date intersection; no zero-filled gaps (spec §7)
        idx = None
        for v in series.values():
            idx = v["local"].index if idx is None else idx.intersection(v["local"].index)
        out_series, stats = [], {}
        if idx is not None and len(idx):
            idx = idx[idx >= idx[-1] - pd.Timedelta(days=365 * years)]
            for sym, v in series.items():
                loc = v["local"].reindex(idx)
                cv = v["conv"].reindex(idx) if v["conv"] is not None else None
                stats[sym] = {"return_local": float(loc.iloc[-1] / loc.iloc[0] - 1),
                              "return_converted": float(cv.iloc[-1] / cv.iloc[0] - 1) if cv is not None else None}
            step = max(1, len(idx) // 260)
            sample = idx[::step]
            if sample[-1] != idx[-1]:
                sample = sample.append(idx[-1:])
            for d in sample:
                row = {"d": d.date().isoformat()}
                for sym, v in series.items():
                    base = v["conv"] if v["conv"] is not None else v["local"]
                    b0 = base.reindex(idx).iloc[0]
                    row[sym] = float(base.loc[d] / b0 * 100)
                out_series.append(row)
        return clean_json({"currency": req.currency, "window": req.window, "items": items, "series": out_series,
                           "period_stats": stats, "dates": {"start": idx[0].date().isoformat() if idx is not None and len(idx) else None,
                                                             "end": idx[-1].date().isoformat() if idx is not None and len(idx) else None,
                                                             "observations": int(len(idx)) if idx is not None else 0},
                           "note": "Rebased to 100 on common trading dates; converted at BoC/ECB reference FX. "
                                   "Non-synchronous closes across exchanges."})


# ------------------------------------------------------------------------------------------------ jobs
class RefreshReq(BaseModel):
    scope: Literal["selection", "universe"] = "selection"
    instrument_ids: list[str] = Field(default_factory=list, max_length=50)
    datasets: list[Literal["profile", "prices", "financials", "estimates", "price_targets", "news",
                           "fund_profile", "holdings"]] | None = None
    mode: Literal["if_stale", "force"] = "if_stale"
    kind: Literal["refresh_prices", "refresh_analysis"] = "refresh_analysis"


@router.post("/refreshes", status_code=202)
def refresh(req: RefreshReq):
    ids = req.instrument_ids if req.scope == "selection" else None
    if req.scope == "selection" and not ids:
        raise HTTPException(422, "instrument_ids required for selection scope")
    datasets = req.datasets or (["prices", "profile"] if req.kind == "refresh_prices" else None)
    job, dedup = jobs.submit(req.kind, ids, datasets, req.mode)
    return {**_job_dict(job), "deduplicated": dedup}


@router.post("/discovery-runs", status_code=202)
def discovery(mode: Literal["if_stale", "force"] = "if_stale"):
    job, dedup = jobs.submit("discovery", None, None, mode)
    return {**_job_dict(job), "deduplicated": dedup}


@router.post("/recalculate", status_code=202)
def recalc():
    job, dedup = jobs.submit("recalculate")
    return {**_job_dict(job), "deduplicated": dedup}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404, "Job not found")
        d = _job_dict(j)
        d["items"] = (j.progress or {}).get("items")
        return d


@router.get("/jobs")
def list_jobs(limit: int = Query(10, le=50)):
    with SessionLocal() as s:
        return [_job_dict(j) for j in s.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))]


# ------------------------------------------------------------------------------------------------ data health
@router.get("/data-health")
def data_health():
    with SessionLocal() as s:
        insts = s.scalars(select(Instrument).where(Instrument.active.is_(True))).all()
        # one grouped query for the latest snapshot per (subject, dataset) instead of ~7k point queries
        from sqlalchemy import and_, func
        latest = (select(DatasetSnapshot.subject, DatasetSnapshot.dataset, func.max(DatasetSnapshot.fetched_at).label("ts"))
                  .group_by(DatasetSnapshot.subject, DatasetSnapshot.dataset).subquery())
        snaps = s.execute(select(DatasetSnapshot.subject, DatasetSnapshot.dataset, DatasetSnapshot.provider, DatasetSnapshot.status,
                                 DatasetSnapshot.fetched_at, DatasetSnapshot.attempts)
                          .join(latest, and_(DatasetSnapshot.subject == latest.c.subject, DatasetSnapshot.dataset == latest.c.dataset,
                                             DatasetSnapshot.fetched_at == latest.c.ts))).all()
        by = {(x.subject, x.dataset): x for x in snaps}
        matrix = []
        for i in sorted(insts, key=lambda x: (x.asset_type != "stock", x.asset_type, x.symbol)):
            row = {"id": i.id, "symbol": i.symbol, "asset_type": i.asset_type, "datasets": {}}
            for ds in datasets_for(i):
                x = by.get((i.id, ds))
                ok = x is not None and x.status == "valid"
                row["datasets"][ds] = None if x is None else {
                    "provider": x.provider, "status": x.status, "fetched_at": x.fetched_at.isoformat(),
                    "attempts": None if ok else x.attempts}  # trails only where something went wrong (keeps payload small)
            matrix.append(row)
        macro = {}
        for ds in ("fx", "cash_rates"):
            snap = latest_snapshot(s, MACRO, ds, valid_only=False)
            macro[ds] = None if snap is None else {"provider": snap.provider, "status": snap.status,
                                                   "fetched_at": snap.fetched_at.isoformat(), "attempts": snap.attempts}
        import datetime as _dt
        today = _dt.date.today().isoformat()
        calls = {k.split("|")[1]: v for k, v in Provider.calls.items() if k.startswith(today)}
        caps = [dict(p.capabilities(), calls_today=calls.get(p.id, 0)) for p in providers().values()]
        cons = latest_snapshot(s, MACRO, "constituents")
        return {"providers": caps,
                "chains": {ds: get_settings().chain(ds) for ds in
                           ["profile", "prices", "financials", "estimates", "price_targets", "news", "fund_profile", "holdings", "fx"]},
                "macro": macro, "matrix": matrix,
                "universe": {"mode": (cons.payload or {}).get("mode") if cons else None,
                             "notes": (cons.payload or {}).get("notes") if cons else [],
                             "fetched_at": cons.fetched_at.isoformat() if cons else None,
                             "stocks": sum(1 for i in insts if i.asset_type == "stock"),
                             "funds": sum(1 for i in insts if i.asset_type in ("etf", "mutualfund", "fund"))}}


# ------------------------------------------------------------------------------------------------ watchlist & notes
@router.put("/watchlist/{iid}")
def watch_add(iid: str):
    with SessionLocal() as s:
        if not s.get(Instrument, iid):
            raise HTTPException(404, "Instrument not found")
        if not s.get(WatchlistItem, iid):
            s.add(WatchlistItem(instrument_id=iid))
            s.commit()
    return {"ok": True}


@router.delete("/watchlist/{iid}")
def watch_remove(iid: str):
    with SessionLocal() as s:
        w = s.get(WatchlistItem, iid)
        if w:
            s.delete(w)
            s.commit()
    return {"ok": True}


class NoteReq(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.get("/instruments/{iid}/notes")
def notes(iid: str):
    with SessionLocal() as s:
        return [{"id": n.id, "body": n.body, "created_at": n.created_at.isoformat()}
                for n in s.scalars(select(Note).where(Note.instrument_id == iid).order_by(Note.created_at.desc()))]


@router.post("/instruments/{iid}/notes", status_code=201)
def add_note(iid: str, req: NoteReq):
    with SessionLocal() as s:
        if not s.get(Instrument, iid):
            raise HTTPException(404, "Instrument not found")
        n = Note(instrument_id=iid, body=req.body)
        s.add(n)
        s.commit()
        return {"id": n.id, "body": n.body, "created_at": n.created_at.isoformat()}


@router.delete("/notes/{nid}")
def delete_note(nid: str):
    with SessionLocal() as s:
        n = s.get(Note, nid)
        if n:
            s.delete(n)
            s.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ funds
@router.get("/instruments/{iid}/holdings")
def holdings(iid: str):
    with SessionLocal() as s:
        snap = latest_snapshot(s, iid, "holdings")
        if not snap:
            raise HTTPException(404, "No holdings")
        return clean_json({"provider": snap.provider, "route": snap.route, "fetched_at": snap.fetched_at.isoformat(),
                           **(snap.payload or {})})


class OverlapReq(BaseModel):
    instrument_ids: list[str] = Field(min_length=2, max_length=8)


@router.post("/overlap")
def overlap_matrix(req: OverlapReq):
    with SessionLocal() as s:
        hs, syms = {}, {}
        for iid in req.instrument_ids:
            inst = s.get(Instrument, iid)
            snap = latest_snapshot(s, iid, "holdings")
            syms[iid] = inst.symbol if inst else iid
            hs[iid] = snap.payload if snap else None
        from ..analytics.funds import expand_holdings
        lookup = {}
        for f in s.scalars(select(Instrument).where(Instrument.asset_type.in_(["etf", "mutualfund", "fund"]))):
            fs = latest_snapshot(s, f.id, "holdings")
            if fs and fs.payload:
                lookup[f.symbol] = fs.payload
        hs = {k: expand_holdings(v, lookup) if v else v for k, v in hs.items()}
        cells = []
        for a in req.instrument_ids:
            for b in req.instrument_ids:
                if a == b:
                    cells.append({"a": syms[a], "b": syms[b], "overlap": 1.0, "common": None, "exact": True})
                else:
                    cells.append({"a": syms[a], "b": syms[b], **fund_overlap(hs[a], hs[b])})
        return {"symbols": [syms[i] for i in req.instrument_ids], "cells": cells,
                "complete": {syms[i]: bool((hs[i] or {}).get("complete")) for i in req.instrument_ids},
                "note": "Σ min(weightᴬ, weightᴮ) over common securities. With top-10 holdings the figure is a lower bound."}


# ------------------------------------------------------------------------------------------------ alerts
@router.get("/alerts")
def alerts(limit: int = Query(50, le=200)):
    with SessionLocal() as s:
        rows = s.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(limit)).all()
        return [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "instrument_id": a.instrument_id,
                 "created_at": a.created_at.isoformat(), "read": a.read} for a in rows]


@router.post("/alerts/read-all")
def alerts_read_all():
    with SessionLocal() as s:
        for a in s.scalars(select(Alert).where(Alert.read.is_(False))):
            a.read = True
        s.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ export (FR10)
@router.get("/exports/{asset}.csv")
def export_csv(asset: Literal["stock", "fund"]):
    with SessionLocal() as s:
        run = latest_completed_run(s)
        if not run:
            raise HTTPException(404, "No snapshot")
        rows = _rows(s, run.id, asset)
        metric_keys = list(rows[0]["metrics"].keys()) if rows else []
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["# snapshot", run.id, "as_of", run.created_at.isoformat(),
                    "source", "see Sources tab per instrument; free providers incl. unofficial Yahoo — personal research use"])
        w.writerow(["symbol", "name", "region", "currency", "price", "price_date", "score", "score_model", "rank",
                    *metric_keys, "data_confidence"])
        for r in sorted(rows, key=lambda r: (r["core"]["score"] is None, -(r["core"]["score"] or 0))):
            m = r["metrics"]
            w.writerow([r["symbol"], r.get("name"), r.get("region"), r.get("currency"),
                        (r.get("price") or {}).get("value"), (r.get("price") or {}).get("date"),
                        r["core"].get("score"), r["core"].get("model") or "core_v1", r["core"].get("rank"),
                        *[(m[k]["value"] if m[k]["status"] == "valid" else m[k]["status"].upper()) for k in metric_keys],
                        (r.get("confidence") or {}).get("score")])
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{asset}s_{run.created_at:%Y%m%d}.csv"'})


# ------------------------------------------------------------------------------------------------ settings (FR11)
class SettingsReq(BaseModel):
    universe: Literal["starter", "core", "extended"] | None = None
    discovery_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str | None = None


@router.get("/settings")
def get_settings_api():
    with SessionLocal() as s:
        return {"universe": get_setting(s, "universe", get_settings().universe),
                "discovery_time": get_setting(s, "discovery_time", "04:00"),
                "timezone": get_setting(s, "timezone", "America/Toronto"),
                "presentation_currency": get_settings().presentation_currency}


@router.put("/settings")
def put_settings_api(req: SettingsReq):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if req.timezone:
        try:
            ZoneInfo(req.timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(422, f"Unknown IANA time zone {req.timezone}")
    with SessionLocal() as s:
        for k in ("universe", "discovery_time", "timezone"):
            v = getattr(req, k)
            if v is not None:
                put_setting(s, k, v)
    return get_settings_api()


# ------------------------------------------------------------------------------------------------ validation
@router.get("/validation")
def validation_report():
    with SessionLocal() as s:
        snap = latest_snapshot(s, "validation", "report", valid_only=False)
        if not snap:
            return {"report": None}
        return clean_json({"report": snap.payload, "generated_at": snap.fetched_at.isoformat()})


@router.post("/validation-runs", status_code=202)
def run_validation_job():
    job, dedup = jobs.submit("validation")
    return {**_job_dict(job), "deduplicated": dedup}


@router.get("/instruments/{iid}/tracking")
def tracking_series(iid: str, years: int = Query(3, ge=1, le=10)):
    """Fund vs its total-return index, rebased to 100 on common dates, in the fund's economic currency."""
    from ..services.universe import FUND_BY_SYMBOL
    with SessionLocal() as s:
        inst = s.get(Instrument, iid)
        if not inst:
            raise HTTPException(404, "Instrument not found")
        meta = ((inst.tags or {}).get("fund") or FUND_BY_SYMBOL.get(inst.symbol) or {})
        idx_sym = meta.get("index")
        if not idx_sym:
            return {"available": False, "reason": "No total-return index mapped for this fund's cohort"}
        idx_inst = s.scalars(select(Instrument).where(Instrument.symbol == idx_sym)).first()
        fs, is_ = latest_snapshot(s, iid, "prices"), latest_snapshot(s, idx_inst.id, "prices") if idx_inst else None
        if not fs or not is_:
            return {"available": False, "reason": "Price history missing"}
        prof = latest_snapshot(s, iid, "profile")
        ccy, sc = normalise_quote_currency((prof.payload or {}).get("quote_currency") if prof else inst.currency)
        f = bars_frame(fs.payload, inst.region or "US", sc)[0]["ac"]
        i = bars_frame(is_.payload, "US", 1.0)[0]["ac"]
        fx_snap = latest_snapshot(s, MACRO, "fx")
        fx = FXTable(fx_snap.payload if fx_snap else None)
        if not meta.get("hedged") and ccy != "USD":
            conv = fx.series_rate("USD", ccy, i.index)
            if conv is not None:
                i = i * conv
        common = f.index.intersection(i.index)
        common = common[common >= common[-1] - pd.Timedelta(days=365 * years)]
        f, i = f.reindex(common), i.reindex(common)
        f, i = f / f.iloc[0] * 100, i / i.iloc[0] * 100
        wk = pd.DataFrame({"fund": f, "index": i}).resample("W-FRI").last().dropna()
        return clean_json({"available": True, "index": idx_sym, "currency": ccy if not meta.get("hedged") else "USD",
                           "hedged": bool(meta.get("hedged")),
                           "series": [{"d": d.date().isoformat(), "fund": r.fund, "index": r["index"],
                                       "gap": r.fund - r["index"]} for d, r in wk.iterrows()]})


# ------------------------------------------------------------------------------------------------ scenarios (spec §8)
class ScenarioReq(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    assumptions: dict
    results: dict | None = None


@router.get("/instruments/{iid}/scenarios")
def list_scenarios(iid: str):
    with SessionLocal() as s:
        rows = s.scalars(select(Scenario).where(Scenario.instrument_id == iid).order_by(Scenario.created_at.desc())).all()
        return [{"id": x.id, "name": x.name, "version": x.version, "author": x.author, "assumptions": x.assumptions,
                 "results": x.results, "created_at": x.created_at.isoformat()} for x in rows]


@router.post("/instruments/{iid}/scenarios", status_code=201)
def save_scenario(iid: str, req: ScenarioReq):
    with SessionLocal() as s:
        if not s.get(Instrument, iid):
            raise HTTPException(404, "Instrument not found")
        prev = s.scalars(select(Scenario).where(Scenario.instrument_id == iid, Scenario.name == req.name)
                         .order_by(Scenario.version.desc())).first()
        sc = Scenario(instrument_id=iid, name=req.name, version=(prev.version + 1) if prev else 1,
                      assumptions=req.assumptions, results=req.results)
        s.add(sc)
        s.commit()
        return {"id": sc.id, "name": sc.name, "version": sc.version, "created_at": sc.created_at.isoformat()}
