"""Ingestion: universe sync, bulk prices, parallel deep enrichment, versioned snapshots, pruning."""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import DatasetSnapshot, Instrument, SessionLocal, WatchlistItem, get_setting, latest_snapshot, utcnow
from ..providers.base import ProviderResult
from ..providers.canonical import region_of
from ..providers.registry import ChainOutcome, fx_providers, providers, run_chain, run_fx_chain
from .universe import BENCHMARKS, FUND_CATALOGUE, INDICES, fetch_constituents

log = logging.getLogger(__name__)

STOCK_DATASETS = ["profile", "prices", "financials", "estimates", "price_targets", "news"]
FUND_DATASETS = ["profile", "prices", "fund_profile", "holdings"]
INDEX_DATASETS = ["prices"]
STALE_AFTER = {"profile": timedelta(days=7), "prices": timedelta(hours=12), "financials": timedelta(days=7),
               "estimates": timedelta(hours=20), "price_targets": timedelta(hours=20), "news": timedelta(hours=12),
               "fund_profile": timedelta(days=7), "holdings": timedelta(days=3),
               "fx": timedelta(hours=12), "cash_rates": timedelta(hours=20), "constituents": timedelta(days=7)}
MACRO = "macro"
KEEP_VERSIONS = {"prices": 2, "holdings": 3}  # large payloads; other datasets keep 30 days of history


def datasets_for(inst: Instrument) -> list[str]:
    if inst.asset_type == "index":
        return INDEX_DATASETS
    if inst.asset_type in ("etf", "mutualfund", "fund"):
        return FUND_DATASETS
    return STOCK_DATASETS


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _compact(dataset: str, payload):
    """Store price bars column-wise with rounding (~60% smaller); readers accept both layouts."""
    if dataset == "prices" and payload and isinstance(payload.get("bars"), list):
        bars = payload["bars"]
        cols = {k: [] for k in ("d", "o", "h", "l", "c", "ac", "v")}
        for b in bars:
            for k in cols:
                v = b.get(k)
                cols[k].append(round(v, 4) if isinstance(v, float) and k != "v" else v)
        payload = {**payload, "bars": cols}
    return payload


def is_stale(s: Session, subject: str, dataset: str) -> bool:
    snap = latest_snapshot(s, subject, dataset, valid_only=True)
    if snap is None:
        return True
    fetched = snap.fetched_at if snap.fetched_at.tzinfo else snap.fetched_at.replace(tzinfo=utcnow().tzinfo)
    if utcnow() - fetched > STALE_AFTER.get(dataset, timedelta(hours=12)):
        return True
    if dataset == "financials":  # earnings-triggered refresh (spec §15): vendor reports a newer quarter
        prof = latest_snapshot(s, subject, "profile")
        mrq = (prof.payload or {}).get("most_recent_quarter") if prof else None
        periods = [p["period_end"] for p in (snap.payload or {}).get("quarterly", []) + (snap.payload or {}).get("annual", [])]
        if mrq and periods and mrq > max(periods):
            return True
    return False


def store(s: Session, subject: str, dataset: str, outcome) -> DatasetSnapshot:
    r = outcome.result
    payload = _compact(dataset, r.payload) if r else None
    snap = DatasetSnapshot(
        subject=subject, dataset=dataset, provider=outcome.provider, route=r.route if r else None,
        status=(r.status if r else "failed"), payload=payload,
        content_hash=_hash(payload) if r else None,
        attempts=outcome.attempts + ([{"notes": r.notes}] if r and r.notes else []))
    s.add(snap)
    s.commit()
    return snap


def ensure_instrument(s: Session, symbol: str, asset_type: str = "stock", tags: dict | None = None) -> Instrument:
    symbol = symbol.strip().upper()
    inst = s.scalars(select(Instrument).where(Instrument.symbol == symbol)).first()
    if inst is None:
        inst = Instrument(symbol=symbol, asset_type=asset_type, region=region_of(symbol), tags=tags or {})
        s.add(inst)
    else:
        if tags is not None:
            inst.tags = {**(inst.tags or {}), **tags}
        # upgrade generic types only; a resolved etf/mutualfund type (from the profile) is kept
        if asset_type in ("fund", "index") and inst.asset_type in ("stock", "benchmark"):
            inst.asset_type = asset_type
    s.commit()
    return inst


def _apply_profile(s: Session, inst: Instrument, p: dict) -> None:
    inst.name = p.get("name") or inst.name
    inst.exchange = p.get("exchange") or inst.exchange
    inst.currency = p.get("quote_currency") or inst.currency
    inst.sector, inst.industry = p.get("sector") or inst.sector, p.get("industry") or inst.industry
    inst.country = p.get("country") or inst.country
    inst.isin = p.get("isin") or inst.isin
    qt = (p.get("quote_type") or "").lower()
    if inst.asset_type in ("fund", "etf", "mutualfund") and qt in ("etf", "mutualfund"):
        inst.asset_type = qt
    s.commit()


def refresh_instrument(s: Session, inst: Instrument, datasets: list[str], mode: str = "if_stale") -> dict:
    """Walks the fallback chain for each dataset. Returns counts + per-dataset outcome."""
    res = {"updated": 0, "unchanged": 0, "failed": 0, "skipped": 0, "datasets": {}}
    for ds in datasets:
        if ds not in datasets_for(inst):
            continue
        if mode == "if_stale" and not is_stale(s, inst.id, ds):
            res["skipped"] += 1
            res["datasets"][ds] = "fresh"
            continue
        prev = latest_snapshot(s, inst.id, ds)
        out = run_chain(ds, inst.symbol, inst.region, name=inst.name)
        snap = store(s, inst.id, ds, out)
        if not out.ok:
            res["failed"] += 1
            res["datasets"][ds] = "failed"
            log.info("%s %s failed: %s", inst.symbol, ds, [a.get("status") for a in out.attempts])
            continue
        res["unchanged" if prev is not None and prev.content_hash == snap.content_hash else "updated"] += 1
        res["datasets"][ds] = f"{snap.status} via {out.provider}"
        if ds == "profile":
            _apply_profile(s, inst, out.result.payload)
    return res


def refresh_prices_bulk(s: Session, insts: list[Instrument], mode: str) -> dict:
    """Bulk-download prices for every instrument that needs them; misses fall back to the per-symbol chain."""
    need = [i for i in insts if mode != "if_stale" or is_stale(s, i.id, "prices")]
    res = {"bulk": 0, "fallback": 0, "failed": 0, "skipped": len(insts) - len(need)}
    if not need:
        return res
    yahoo = providers()["yahoo"]
    got = yahoo.fetch_prices_bulk([i.symbol for i in need]) if "yahoo" in get_settings().chain("prices") else {}
    for inst in need:
        if inst.symbol in got:
            payload = got[inst.symbol]
            prof = latest_snapshot(s, inst.id, "profile")
            payload["quote_currency"] = (prof.payload or {}).get("quote_currency") if prof else inst.currency
            store(s, inst.id, "prices", ChainOutcome("prices", provider="yahoo",
                                                    result=ProviderResult(payload=payload, route="yfinance.download (bulk, 10y)"),
                                                    attempts=[{"provider": "yahoo-bulk", "status": "ok"}]))
            res["bulk"] += 1
        else:
            out = run_chain("prices", inst.symbol, inst.region)
            store(s, inst.id, "prices", out)
            res["fallback" if out.ok else "failed"] += 1
    return res


def refresh_many(insts: list[Instrument], datasets: list[str] | None, mode: str,
                 progress: Callable[[dict], None] | None = None) -> dict:
    """Prices in bulk, then the other datasets in parallel (one DB session per worker)."""
    tot = {"requested": len(insts), "done": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0, "current": None}
    ids = [i.id for i in insts]
    if datasets is None or "prices" in datasets:
        tot["current"] = "bulk prices"
        if progress:
            progress(tot)
        with SessionLocal() as s:
            pr = refresh_prices_bulk(s, [s.get(Instrument, i) for i in ids], mode)
        tot["updated"] += pr["bulk"] + pr["fallback"]
        tot["failed"] += pr["failed"]
        tot["skipped"] += pr["skipped"]
        tot["prices"] = pr
    rest = [d for d in (datasets or STOCK_DATASETS + FUND_DATASETS) if d != "prices"]

    def work(iid: str) -> tuple[str, dict]:
        with SessionLocal() as s:
            inst = s.get(Instrument, iid)
            ds = [d for d in ["profile"] + rest if d in datasets_for(inst) and (d in rest or datasets is None)]
            ds = list(dict.fromkeys(ds))
            return inst.symbol, refresh_instrument(s, inst, ds, mode)

    with ThreadPoolExecutor(max_workers=max(1, get_settings().ingest_workers)) as ex:
        futs = [ex.submit(work, i) for i in ids]
        for f in as_completed(futs):
            try:
                sym, r = f.result()
                for k in ("updated", "unchanged", "failed", "skipped"):
                    tot[k] += r[k]
                tot["current"] = sym
            except Exception as e:  # noqa: BLE001
                log.exception("ingest worker failed: %s", e)
                tot["failed"] += 1
            tot["done"] += 1
            if progress and (tot["done"] % 5 == 0 or tot["done"] == len(ids)):
                progress(dict(tot))
    tot["current"] = None
    return tot


def refresh_macro(s: Session, mode: str = "if_stale") -> dict:
    res = {}
    if mode != "if_stale" or is_stale(s, MACRO, "fx"):
        out = run_fx_chain()
        store(s, MACRO, "fx", out)
        res["fx"] = out.provider or "failed"
    if mode != "if_stale" or is_stale(s, MACRO, "cash_rates"):
        series, attempts = fx_providers()["_cash"].fetch_all()
        result = (ProviderResult(payload=series, route="BoC / FRED / BoE",
                                 status="valid" if len(series) == 3 else "partial") if series else None)
        store(s, MACRO, "cash_rates", ChainOutcome("cash_rates", provider="central_banks", result=result, attempts=attempts))
        res["cash_rates"] = list(series)
    return res


def sync_universe(s: Session, mode: str = "if_stale") -> dict:
    """Synchronise stock constituents, the fund catalogue, benchmarks and TR indices (spec §4 step 1).
    Delisted / dropped stocks are deactivated, never deleted (history preserved)."""
    universe = get_setting(s, "universe", get_settings().universe)
    snap = latest_snapshot(s, MACRO, "constituents")
    if mode == "if_stale" and snap and not is_stale(s, MACRO, "constituents") and (snap.payload or {}).get("mode") == universe:
        members, notes = snap.payload["members"], ["constituents fresh"]
    else:
        members, notes = fetch_constituents(universe)
        prev = (snap.payload or {}).get("members") if snap else None
        if prev and len(members) < 0.8 * len(prev) and (snap.payload or {}).get("mode") == universe:
            notes.append("New list < 80% of previous — keeping previous list (likely a source failure)")
            members = prev
        store(s, MACRO, "constituents", ChainOutcome("constituents", provider="wikipedia/issuer",
              result=ProviderResult(payload={"mode": universe, "members": members, "notes": notes}, route="constituents"),
              attempts=[]))
    keep = {m["symbol"] for m in members}
    for m in members:
        ensure_instrument(s, m["symbol"], "stock", tags={"indices": m["indices"]})
    for f in FUND_CATALOGUE:
        ensure_instrument(s, f["symbol"], "fund", tags={"fund": {k: v for k, v in f.items() if k != "symbol"}})
    for region, (sym, name) in BENCHMARKS.items():
        inst = ensure_instrument(s, sym, "fund", tags={"benchmark_for": region})
        inst.name = inst.name or name
    for sym, (name, ccy) in INDICES.items():
        inst = ensure_instrument(s, sym, "index", tags={"index": True})
        inst.name, inst.currency, inst.region = name, ccy, "US"
        inst.asset_type = "index"
    watched = {w.instrument_id for w in s.scalars(select(WatchlistItem))}
    deactivated = 0
    for inst in s.scalars(select(Instrument).where(Instrument.asset_type == "stock")):
        should = inst.symbol in keep or inst.id in watched or (inst.tags or {}).get("user_added")
        if inst.active != bool(should):
            inst.active = bool(should)
            deactivated += 0 if should else 1
    s.commit()
    n_funds = len(FUND_CATALOGUE)
    return {"universe": universe, "stocks": len(keep), "funds": n_funds, "deactivated": deactivated, "notes": notes}


def prune(s: Session) -> int:
    """Retention (spec §19): large datasets keep the last N versions; others 30 days (latest always kept)."""
    removed = 0
    cutoff = utcnow() - timedelta(days=30)
    for ds, keep in KEEP_VERSIONS.items():
        subjects = s.execute(select(DatasetSnapshot.subject).where(DatasetSnapshot.dataset == ds)
                             .group_by(DatasetSnapshot.subject).having(func.count() > keep)).scalars().all()
        for subj in subjects:
            ids = s.scalars(select(DatasetSnapshot.id).where(DatasetSnapshot.subject == subj, DatasetSnapshot.dataset == ds)
                            .order_by(DatasetSnapshot.fetched_at.desc()).offset(keep)).all()
            if ids:
                removed += s.execute(delete(DatasetSnapshot).where(DatasetSnapshot.id.in_(ids))).rowcount
    old = s.scalars(select(DatasetSnapshot.id).where(DatasetSnapshot.fetched_at < cutoff,
                                                    DatasetSnapshot.dataset.notin_(list(KEEP_VERSIONS)))).all()
    for oid in old:
        snap = s.get(DatasetSnapshot, oid)
        newest = latest_snapshot(s, snap.subject, snap.dataset, valid_only=False)
        if newest and newest.id != oid:
            s.delete(snap)
            removed += 1
    s.commit()
    return removed


def today() -> date:
    return date.today()
