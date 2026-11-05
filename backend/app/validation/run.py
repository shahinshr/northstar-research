"""Validation suite — "is the data right, and does the recommendation mean anything?"

1. Data cross-checks against independent sources (spec §14 cross-provider checks)
   a. Last close vs Finnhub quote (threshold 0.5%)
   b. Latest fiscal-year revenue / net income vs SEC EDGAR 10-K (threshold 2%)
   c. Our P/E, operating margin, market cap vs Finnhub basic financials (definitional comparison)
2. Walk-forward backtest of core_v1 on US large caps with SEC data available only 90 days after period
   end (point-in-time approximation), quarterly rebalance, forward 3-month total returns.
3. Agreement with external opinions (analyst target upside / consensus rating) — context, not proof.
4. Funds: does cost predict tracking outcome within the S&P 500 cohort? (Morningstar's fee finding)
The report states its own biases; it is evidence, not a guarantee of future returns.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import select

from ..analytics import scoring
from ..analytics.market import bars_frame
from ..config import BACKEND_DIR, get_settings
from ..db import AnalysisResult, DatasetSnapshot, Instrument, SessionLocal, latest_completed_run, latest_snapshot
from ..providers.base import ProviderResult
from ..providers.canonical import normalise_quote_currency
from ..providers.registry import ChainOutcome
from ..services.ingest import store

log = logging.getLogger(__name__)
CACHE = BACKEND_DIR / "data" / "validation"
LAG_DAYS = 90  # filings assumed public 90 days after fiscal period end (10-K deadline 60-90 days)
COST_BP = 10   # one-way transaction cost per unit turnover


def spearman(a, b) -> float:
    """Rank correlation without scipy: Pearson correlation of average ranks (NaN pairs dropped)."""
    x, y = pd.Series(list(a), dtype=float), pd.Series(list(b), dtype=float)
    ok = x.notna() & y.notna()
    if ok.sum() < 3:
        return float("nan")
    return float(x[ok].rank().corr(y[ok].rank()))


def _ua() -> dict:
    return {"User-Agent": get_settings().sec_user_agent}


# ============================================================================== 1. data cross-checks
def _finnhub(path: str, **params):
    key = get_settings().finnhub_api_key
    if not key:
        return None
    time.sleep(1.05)
    r = httpx.get(f"https://finnhub.io/api/v1{path}", params={**params, "token": key}, timeout=20)
    return r.json() if r.status_code == 200 else None


def _sec_fy(symbol: str, cik_map: dict) -> dict:
    cik = cik_map.get(symbol.replace(".", "-"))
    if not cik:
        return {}
    time.sleep(0.15)
    r = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=_ua(), timeout=60)
    if r.status_code != 200:
        return {}
    facts = r.json().get("facts", {}).get("us-gaap", {})
    out = {}
    for field, concepts in (("revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]),
                            ("net_income", ["NetIncomeLoss"])):
        best = {}
        for c in concepts:
            for row in (facts.get(c, {}).get("units", {}).get("USD") or []):
                if row.get("form") != "10-K" or "start" not in row:
                    continue
                days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                if 350 <= days <= 380 and row["end"] not in best:
                    best[row["end"]] = row["val"]
            if best:
                break
        out[field] = best
    return out


def data_crosschecks(progress: Callable[[str], None]) -> dict:
    with SessionLocal() as s:
        run = latest_completed_run(s)
        rows = {r.instrument_id: r.payload for r in s.scalars(select(AnalysisResult).where(
            AnalysisResult.run_id == run.id, AnalysisResult.asset_class == "stock"))}
    us = [p for p in rows.values() if p.get("status") == "ok" and p.get("region") == "US"]
    random.Random(42).shuffle(us)  # deterministic stratified-ish sample
    sample = us[:40]
    cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in
               httpx.get("https://www.sec.gov/files/company_tickers.json", headers=_ua(), timeout=30).json().values()}
    price_checks, fin_checks, metric_checks = [], [], []
    for n, p in enumerate(sample, 1):
        if n % 10 == 0:
            progress(f"Cross-checking data {n}/{len(sample)}")
        sym = p["symbol"]
        q = _finnhub("/quote", symbol=sym)
        ours = (p.get("price") or {}).get("value")
        if q and ours and q.get("c"):
            cands = [x for x in (q.get("c"), q.get("pc")) if x]
            diff = min(abs(ours / x - 1) for x in cands)
            price_checks.append({"symbol": sym, "ours": ours, "finnhub": q.get("pc") if abs(ours / q["pc"] - 1) <= abs(ours / q["c"] - 1) else q["c"],
                                 "diff": diff, "pass": diff <= 0.005})
        # statements vs SEC (latest FY we used)
        with SessionLocal() as s:
            fin = latest_snapshot(s, p["id"], "financials")
        if fin and fin.payload.get("annual"):
            fy = sorted(fin.payload["annual"], key=lambda x: x["period_end"], reverse=True)[0]
            sec = _sec_fy(sym, cik_map)
            for field in ("revenue", "net_income"):
                ours_v = fy["fields"].get(field)
                sec_v = (sec.get(field) or {}).get(fy["period_end"])
                if ours_v is not None and sec_v:
                    d = abs(ours_v / sec_v - 1)
                    fin_checks.append({"symbol": sym, "field": field, "period": fy["period_end"], "ours": ours_v,
                                       "sec": sec_v, "diff": d, "pass": d <= 0.02})
        met = _finnhub("/stock/metric", symbol=sym, metric="all")
        mm = (met or {}).get("metric") or {}
        m = p.get("metrics") or {}

        def mv(k):
            x = m.get(k) or {}
            return x.get("value") if x.get("status") == "valid" else None
        pairs = [("pe", mv("pe"), mm.get("peTTM") or mm.get("peBasicExclExtraTTM"), 0.10, "rel"),
                 ("operating_margin", mv("operating_margin"), (mm.get("operatingMarginTTM") or 0) / 100 if mm.get("operatingMarginTTM") is not None else None, 0.02, "abs"),
                 ("market_cap_usd", (p.get("market_cap") or {}).get("usd"), (mm.get("marketCapitalization") or 0) * 1e6 or None, 0.03, "rel")]
        for k, a, b, tol, kind in pairs:
            if a is not None and b:
                d = abs(a / b - 1) if kind == "rel" else abs(a - b)
                metric_checks.append({"symbol": sym, "metric": k, "ours": a, "finnhub": b, "diff": d, "tolerance": tol,
                                      "kind": kind, "pass": d <= tol})

    def summ(rows, by=None):
        if not rows:
            return {"n": 0}
        out = {"n": len(rows), "pass_rate": float(np.mean([r["pass"] for r in rows])),
               "median_diff": float(np.median([r["diff"] for r in rows])),
               "exceptions": sorted([r for r in rows if not r["pass"]], key=lambda r: -r["diff"])[:8]}
        if by:
            out["by"] = {k: summ([r for r in rows if r[by] == k]) for k in sorted({r[by] for r in rows})}
            for v in out["by"].values():
                v.pop("exceptions", None)
        return out
    return {"sample": [p["symbol"] for p in sample], "prices_vs_finnhub": summ(price_checks),
            "statements_vs_sec": summ(fin_checks, "field"), "metrics_vs_finnhub": summ(metric_checks, "metric"),
            "notes": ["Prices: last completed close vs Finnhub current/previous close (0.5% threshold, spec §14).",
                      "Statements: same fiscal period, Yahoo vs SEC 10-K as filed (2% threshold, spec §14).",
                      "Metrics: Finnhub uses its own definitions (e.g. TTM windows, share counts) — differences "
                      "beyond tolerance are flagged for review, not automatically errors."]}


# ============================================================================== 2. walk-forward backtest
FRAME_CONCEPTS = {  # field -> [(concept, unit, instant?)]
    "revenue": [("Revenues", "USD", False), ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", False)],
    "net_income": [("NetIncomeLoss", "USD", False)],
    "operating_income": [("OperatingIncomeLoss", "USD", False)],
    "ocf": [("NetCashProvidedByUsedInOperatingActivities", "USD", False)],
    "capex": [("PaymentsToAcquirePropertyPlantAndEquipment", "USD", False)],
    "d_and_a": [("DepreciationDepletionAndAmortization", "USD", False), ("DepreciationAndAmortization", "USD", False)],
    "interest": [("InterestExpense", "USD", False)],
    "tax": [("IncomeTaxExpenseBenefit", "USD", False)],
    "eps": [("EarningsPerShareDiluted", "USD-per-shares", False)],
    "shares": [("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False)],
    "equity": [("StockholdersEquity", "USD", True)],
    "debt": [("LongTermDebt", "USD", True), ("LongTermDebtNoncurrent", "USD", True)],
    "cash": [("CashAndCashEquivalentsAtCarryingValue", "USD", True)],
}


def _frame(concept: str, unit: str, year: int, instant: bool) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = f"CY{year}Q4I" if instant else f"CY{year}"
    f = CACHE / f"{concept}_{unit}_{tag}.json"
    if f.exists():
        return json.loads(f.read_text())
    time.sleep(0.12)
    r = httpx.get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/{tag}.json", headers=_ua(), timeout=90)
    data = r.json().get("data", []) if r.status_code == 200 else []
    slim = [{"cik": d["cik"], "end": d["end"], "val": d["val"]} for d in data]
    f.write_text(json.dumps(slim))
    return slim


def load_fundamentals(ciks: set[int], years: range, progress) -> dict[int, dict[int, dict]]:
    """-> {cik: {fiscal_year_frame: {field: value, '_end': period_end}}}"""
    out: dict[int, dict[int, dict]] = {}
    total = len(FRAME_CONCEPTS) * len(years)
    n = 0
    for field, options in FRAME_CONCEPTS.items():
        for y in years:
            n += 1
            if n % 20 == 0:
                progress(f"Loading SEC frames {n}/{total}")
            for concept, unit, instant in options:
                # later concepts in the list only fill gaps left by earlier ones
                for d in _frame(concept, unit, y, instant):
                    if d["cik"] in ciks:
                        rec = out.setdefault(d["cik"], {}).setdefault(y, {})
                        if field not in rec:
                            rec[field] = d["val"]
                            if not instant:
                                rec["_end"] = max(rec.get("_end", ""), d["end"])
    return out


def _metrics_at(fy: dict, fy_prev: dict | None, price: float, mom: float | None) -> dict:
    def M(v):
        return {"value": v, "status": "valid"} if v is not None and math.isfinite(v) else {"value": None, "status": "na"}
    sh = fy.get("shares")
    mcap = price * sh if sh else None
    ni, rev, oi, ocf = fy.get("net_income"), fy.get("revenue"), fy.get("operating_income"), fy.get("ocf")
    capex, da, debt, cash, eq = fy.get("capex") or 0, fy.get("d_and_a"), fy.get("debt") or 0, fy.get("cash") or 0, fy.get("equity")
    ebitda = oi + da if oi is not None and da is not None else None
    ev = mcap + debt - cash if mcap else None
    m = {
        "fcf_yield": M((ocf - capex) / mcap if ocf is not None and mcap else None),
        "earnings_yield": M(fy.get("eps") / price if fy.get("eps") is not None and price else (ni / mcap if ni is not None and mcap else None)),
        "ebitda_to_ev": M(ebitda / ev if ebitda and ebitda > 0 and ev and ev > 0 else None),
        "operating_margin": M(oi / rev if oi is not None and rev and rev > 0 else None),
        "cash_conversion": M(ocf / ni if ocf is not None and ni and ni > 0 else None),
        "net_debt_ebitda": M((debt - cash) / ebitda if ebitda and ebitda > 0 else None),
        "interest_coverage": M(oi / fy["interest"] if oi and oi > 0 and fy.get("interest") and fy["interest"] > 0 else None),
        "momentum_12_1": M(mom),
    }
    ic = debt + (eq or 0) - cash
    tax_rate = 0.21
    m["roic"] = M(oi * (1 - tax_rate) / ic if oi is not None and eq is not None and ic > 0 else None)
    if fy_prev:
        r0, r1 = rev, fy_prev.get("revenue")
        e0, e1 = fy.get("eps"), fy_prev.get("eps")
        m["revenue_growth"] = M(r0 / r1 - 1 if r0 and r1 and r1 > 0 else None)
        m["eps_growth"] = M(e0 / e1 - 1 if e0 is not None and e1 and e1 > 0 else None)
    else:
        m["revenue_growth"] = M(None)
        m["eps_growth"] = M(None)
    m["_profitable"] = ni is not None and ni > 0
    return m


def backtest(progress: Callable[[str], None]) -> dict:
    # --- universe: current S&P 500 members we hold prices for, excluding financials/real estate (model scope)
    with SessionLocal() as s:
        insts = s.scalars(select(Instrument).where(Instrument.asset_type == "stock", Instrument.active.is_(True))).all()
        members = [i for i in insts if "S&P 500" in ((i.tags or {}).get("indices") or [])]
        prices, sectors = {}, {}
        for i in members:
            snap = latest_snapshot(s, i.id, "prices")
            if not snap:
                continue
            df, _ = bars_frame(snap.payload, "US", 1.0)
            if len(df) > 600:
                prices[i.symbol] = df["ac"]
                sectors[i.symbol] = i.sector or "Unknown"
        spy = s.scalars(select(Instrument).where(Instrument.symbol == "SPY")).first()
        spy_px = None
        if spy:
            ss = latest_snapshot(s, spy.id, "prices")
            spy_px = bars_frame(ss.payload, "US", 1.0)[0]["ac"] if ss else None
    excluded_sectors = {"Financial Services", "Real Estate"}
    syms = [x for x in prices if sectors.get(x) not in excluded_sectors]
    cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in
               httpx.get("https://www.sec.gov/files/company_tickers.json", headers=_ua(), timeout=30).json().values()}
    sym_cik = {x: cik_map[x.replace(".", "-")] for x in syms if x.replace(".", "-") in cik_map}
    px = pd.DataFrame({x: prices[x] for x in sym_cik}).sort_index()
    start_year = px.index[0].year
    fund = load_fundamentals(set(sym_cik.values()), range(start_year - 2, date.today().year), progress)

    # --- quarterly rebalance dates with 12 months of price history for momentum
    dates = pd.date_range(px.index[0] + pd.Timedelta(days=400), px.index[-1] - pd.Timedelta(days=95), freq="QE")
    periods, turnover_prev = [], None
    comp_names = list(scoring.CORE_V1["components"])
    progress(f"Backtesting {len(sym_cik)} stocks over {len(dates)} quarters")
    for t in dates:
        t_px = px[:t].iloc[-1]
        fwd_end = t + pd.DateOffset(months=3)
        f_px = px[:fwd_end].iloc[-1]
        subjects = {}
        for sym, cik in sym_cik.items():
            p0 = t_px.get(sym)
            if not p0 or pd.isna(p0) or pd.isna(f_px.get(sym)):
                continue
            hist = px[sym][:t].dropna()
            if len(hist) < 260:
                continue
            mom = float(hist.iloc[-22] / hist.iloc[-253] - 1)
            # latest fiscal year whose period end + LAG is before t (point-in-time approximation)
            years = sorted(((y, r) for y, r in fund.get(cik, {}).items() if r.get("_end")
                            and date.fromisoformat(r["_end"]) + timedelta(days=LAG_DAYS) <= t.date()), key=lambda z: z[0])
            if not years:
                continue
            fy = years[-1][1]
            prev = years[-2][1] if len(years) > 1 else None
            m = _metrics_at(fy, prev, float(p0), mom)
            if not m.pop("_profitable"):
                continue  # loss-makers are outside core_v1 scope, as in the live model
            subjects[sym] = {"metrics": m, "sector": sectors[sym], "region": "US",
                             "fwd": float(f_px[sym] / p0 - 1)}
        if len(subjects) < 50:
            continue
        pct = scoring.peer_percentiles(subjects, scoring.all_metric_keys(scoring.CORE_V1), 20,
                                       levels=("sector", "universe"))
        scored = []
        for sym, v in subjects.items():
            r = scoring.score_model(scoring.CORE_V1, v["metrics"], pct.get(sym, {}))
            if r["score"] is not None:
                scored.append((sym, r["score"], v["fwd"], {c: r["components"][c]["score"] for c in comp_names}))
        if len(scored) < 50:
            continue
        df = pd.DataFrame(scored, columns=["sym", "score", "fwd", "comps"])
        df["q"] = pd.qcut(df["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
        top = set(df[df.q == 5].sym)
        turnover = 1 - len(top & turnover_prev) / len(top) if turnover_prev else 1.0
        turnover_prev = top
        comp_ic = {c: spearman([x[c] for x in df["comps"]], df["fwd"]) for c in comp_names}
        spy_ret = None
        if spy_px is not None:
            a, b = spy_px[:t], spy_px[:fwd_end]
            spy_ret = float(b.iloc[-1] / a.iloc[-1] - 1) if len(a) and len(b) else None
        periods.append({"date": t.date().isoformat(), "n": len(df), "ic": spearman(df["score"], df["fwd"]),
                        "q": {int(k): float(v) for k, v in df.groupby("q")["fwd"].mean().items()},
                        "ew": float(df["fwd"].mean()), "spy": spy_ret, "turnover_top": turnover, "comp_ic": comp_ic})
    if not periods:
        return {"error": "Not enough overlapping price and SEC data to backtest"}
    yrs = len(periods) / 4

    def ann(rets):
        return float(np.prod([1 + r for r in rets]) ** (1 / yrs) - 1)
    q_ann = {q: ann([p["q"].get(q, 0) for p in periods]) for q in range(1, 6)}
    top_net = ann([p["q"][5] - p["turnover_top"] * 2 * COST_BP / 1e4 for p in periods])
    ics = np.array([p["ic"] for p in periods])
    hit = float(np.mean([p["q"][5] > p["ew"] for p in periods]))
    spy_rets = [p["spy"] for p in periods if p["spy"] is not None]
    comp_mean_ic = {c: float(np.nanmean([p["comp_ic"][c] for p in periods])) for c in comp_names}
    cum = []
    w = {"top": 1.0, "ew": 1.0, "bottom": 1.0, "spy": 1.0}
    for p in periods:
        w["top"] *= 1 + p["q"][5]
        w["ew"] *= 1 + p["ew"]
        w["bottom"] *= 1 + p["q"][1]
        w["spy"] *= 1 + (p["spy"] or 0)
        cum.append({"d": p["date"], **{k: round(v * 100, 2) for k, v in w.items()}})
    return {
        "universe": f"{len(sym_cik)} current S&P 500 members (ex financials & real estate), profitable at each date",
        "period": f"{periods[0]['date']} → {periods[-1]['date']}", "rebalances": len(periods),
        "forward_horizon": "3 months", "pit_lag_days": LAG_DAYS, "cost_bp_one_way": COST_BP,
        "quintile_annual_returns": q_ann, "top_quintile_net_of_costs": top_net,
        "equal_weight_annual": ann([p["ew"] for p in periods]),
        "spy_annual": ann(spy_rets) if len(spy_rets) == len(periods) else None,
        "spread_top_minus_bottom": q_ann[5] - q_ann[1],
        "mean_ic": float(ics.mean()), "ic_t_stat": float(ics.mean() / (ics.std(ddof=1) / math.sqrt(len(ics)))),
        "ic_positive_share": float((ics > 0).mean()), "top_beats_ew_share": hit,
        "avg_top_turnover": float(np.mean([p["turnover_top"] for p in periods[1:]])) if len(periods) > 1 else None,
        "component_mean_ic": comp_mean_ic, "cumulative": cum, "periods": periods,
        "biases": [
            "Survivorship: universe is TODAY's S&P 500, so past losers that left the index are missing — absolute returns "
            "are inflated for every quintile; the spread between quintiles is less affected but not immune.",
            "Restatements: SEC frames give the latest reported value; data is lagged 90 days after period end to approximate "
            "point-in-time availability (spec §20: label as descriptive unless true PIT data is licensed).",
            "Annual statements only (no TTM), US large caps only, no estimate-revision factor (no historical consensus).",
            f"Transaction costs: {COST_BP} bp one-way on top-quintile turnover; no taxes, no market impact.",
        ],
    }


# ============================================================================== 3. external opinions
def analyst_agreement() -> dict:
    with SessionLocal() as s:
        run = latest_completed_run(s)
        rows = [r.payload for r in s.scalars(select(AnalysisResult).where(AnalysisResult.run_id == run.id,
                                                                        AnalysisResult.asset_class == "stock"))]
        recs = {}
        for p in rows:
            prof = latest_snapshot(s, p["id"], "profile")
            vm = ((prof.payload or {}).get("vendor_metrics") or {}) if prof else {}
            recs[p["id"]] = vm.get("recommendationMean")
    data = []
    for p in rows:
        core = ((p.get("scores") or {}).get("core") or {}).get("score")
        up = ((p.get("metrics") or {}).get("target_upside") or {})
        if core is not None:
            data.append({"score": core, "upside": up.get("value") if up.get("status") == "valid" else None,
                         "rec": recs.get(p["id"])})
    df = pd.DataFrame(data)
    if df.empty:
        return {"n": 0}
    out = {"n": len(df)}
    d1 = df.dropna(subset=["upside"])
    out["corr_score_vs_target_upside"] = spearman(d1["score"], d1["upside"]) if len(d1) > 20 else None
    d2 = df.dropna(subset=["rec"])
    out["corr_score_vs_rating"] = -spearman(d2["score"], d2["rec"]) if len(d2) > 20 else None
    if len(d2) > 20:
        d2 = d2.assign(q=pd.qcut(d2["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int))
        out["avg_rating_by_quintile"] = {int(k): float(v) for k, v in d2.groupby("q")["rec"].mean().items()}
    out["note"] = ("Ratings: 1 = strong buy … 5 = sell (Yahoo consensus). Positive correlation = agreement. Low agreement is "
                   "expected: analysts weight forecasts and targets; core_v1 ranks current value/quality/growth vs peers.")
    return out


# ============================================================================== 4. funds: does cost predict outcome?
def fund_cost_check() -> dict:
    with SessionLocal() as s:
        run = latest_completed_run(s)
        rows = [r.row for r in s.scalars(select(AnalysisResult).where(AnalysisResult.run_id == run.id,
                                                                    AnalysisResult.asset_class == "fund"))]
    cohort = [r for r in rows if r and r.get("cohort") == "US large cap · S&P 500"]
    pts = []
    for r in cohort:
        er = r["metrics"]["expense_ratio"]
        td = r["metrics"]["tracking_difference"]
        if er["status"] == "valid" and td["status"] == "valid":
            pts.append({"symbol": r["symbol"], "currency": r.get("currency"), "hedged": r.get("hedged"),
                        "expense_ratio": er["value"], "tracking_difference": td["value"],
                        "residual": td["value"] + er["value"], "score": (r.get("core") or {}).get("score")})
    out = {"cohort": "US large cap · S&P 500", "funds": pts}
    unhedged = [p for p in pts if not p["hedged"]]
    if len(unhedged) >= 4:
        a = pd.DataFrame(unhedged)
        out["corr_expense_vs_tracking"] = spearman(a["expense_ratio"], a["tracking_difference"])
        out["us_domiciled_mean_residual"] = float(a[a.currency == "USD"]["residual"].mean()) if (a.currency == "USD").any() else None
        out["foreign_domiciled_mean_residual"] = float(a[a.currency != "USD"]["residual"].mean()) if (a.currency != "USD").any() else None
    out["note"] = ("Tracking difference (3y, annualised) vs the S&P 500 total-return index. Residual = TD + expense ratio: "
                   "≈0 for US-domiciled funds; negative for Canadian/UK-listed funds (≈15% withholding tax on US dividends, "
                   "~0.2%/yr) — a real cost that the cost-only view misses. Negative correlation = higher fees → worse outcome.")
    return out


# ============================================================================== orchestrator
def run_validation(progress: Callable[[str], None]) -> str:
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name, fn in (("data", lambda: data_crosschecks(progress)), ("backtest", lambda: backtest(progress)),
                     ("analysts", analyst_agreement), ("funds", fund_cost_check)):
        progress(f"Validation: {name}")
        try:
            report[name] = fn()
        except Exception as e:  # noqa: BLE001 - one failed section must not lose the others
            log.exception("validation section %s failed", name)
            report[name] = {"error": f"{type(e).__name__}: {e}"}
    with SessionLocal() as s:
        store(s, "validation", "report", ChainOutcome("report", provider="validation-suite",
                                                    result=ProviderResult(payload=report, route="validation/run.py"),
                                                    attempts=[]))
    bt = report.get("backtest") or {}
    if "mean_ic" in bt:
        return (f"Backtest IC {bt['mean_ic']:.3f} (t={bt['ic_t_stat']:.1f}); top quintile {bt['quintile_annual_returns'][5]:.1%}/yr "
                f"vs equal-weight {bt['equal_weight_annual']:.1%}")
    return "Validation complete (see report)"
