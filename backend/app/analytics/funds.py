"""Fund & ETF analytics (spec §7).

Conventions
* Costs are fractions (0.0009 = 0.09%). Never subtracted again from NAV/price returns (already net).
* Tracking difference = annualised fund TR − annualised index TR over the same dates, in the fund's
  economic currency (unhedged: index converted to the fund's trading currency; hedged: compared in the
  index currency, the residual includes hedging cost/carry). Tracking error uses WEEKLY returns to limit
  non-synchronous close effects between exchanges. The index is gross of withholding tax, so funds that
  hold foreign stocks show a structural drag — that is real investor cost, not an error.
* Look-through valuation = Σ wᵢ·EYᵢ over mapped holdings (never an average of P/Es); implied P/E = 1/EY
  only when ≥ 80% of weight is mapped, otherwise reported as partial.
* Overlap of two long-only portfolios = Σ min(wᵢᴬ, wᵢᴮ) over common securities; with top-10 data the
  result is a lower bound.
"""
from __future__ import annotations

from datetime import date

import re

import numpy as np
import pandas as pd

from .fundamentals import M

TW = 52  # weeks per year


def cost_metrics(fp: dict) -> dict:
    out = {}
    er = fp.get("expense_ratio")
    out["expense_ratio"] = M(er, fp.get("expense_ratio_source") or "", unit="ratio") if er is not None else \
        M(None, reason="Expense ratio not published by any provider in the chain (add FMP/EODHD key or factsheet)")
    cat = fp.get("category_expense_ratio")
    if er is not None and cat:
        out["cost_vs_category"] = M(er - cat, f"vs category average {cat:.2%} (Yahoo/Morningstar category)")
    out["turnover"] = M(fp.get("turnover"), "annual holdings turnover") if fp.get("turnover") is not None else M(None, reason="Not reported")
    return out


def scale_metrics(fp: dict, aum_usd: float | None) -> dict:
    out = {"aum_usd": M(aum_usd, fp.get("aum_note") or "total assets", unit="USD") if aum_usd else M(None, reason="AUM not reported")}
    inc = fp.get("inception")
    if inc:
        try:
            out["age_years"] = M((date.today() - date.fromisoformat(inc[:10])).days / 365.25, f"since {inc[:10]}", unit="years")
        except ValueError:
            out["age_years"] = M(None, reason="Inception date unparseable")
    else:
        out["age_years"] = M(None, reason="Inception date not reported")
    out["distribution_yield"] = M(fp.get("distribution_yield"), "trailing distribution yield (vendor)") \
        if fp.get("distribution_yield") is not None else M(None, reason="Not reported")
    return out


FUND_NAME = re.compile(r"\b(ETF|UCITS|Index Fund|Fund ETF)\b", re.I)


def fund_share(hold: dict | None, fund_symbols: set[str]) -> float:
    """Portfolio weight held in other funds (by catalogue symbol or fund-like name)."""
    rows = (hold or {}).get("holdings") or []
    return sum(r["weight"] for r in rows if r.get("symbol") in fund_symbols or FUND_NAME.search(str(r.get("name") or "")))


def expand_holdings(hold: dict | None, lookup: dict[str, dict], depth: int = 2, _seen: frozenset = frozenset()) -> dict | None:
    """Look through nested funds (spec §7): replace a fund holding by its own holdings × weight, up to `depth`
    levels, stopping on cycles. The result is complete only if every expanded level was complete."""
    if not hold:
        return hold
    out: dict[str, dict] = {}
    complete = bool(hold.get("complete"))
    expanded = []
    for r in hold.get("holdings") or []:
        sym, w = r.get("symbol"), r.get("weight") or 0
        sub = lookup.get(sym)
        if sub and depth > 0 and sym not in _seen and sub.get("holdings"):
            inner = expand_holdings(sub, lookup, depth - 1, _seen | {sym})
            complete = complete and bool(inner.get("complete"))
            expanded.append(sym)
            for x in inner["holdings"]:
                cur = out.setdefault(x["symbol"], {**x, "weight": 0.0})
                cur["weight"] += w * x["weight"]
        else:
            cur = out.setdefault(sym, {**r, "weight": 0.0})
            cur["weight"] += w
    return {**hold, "holdings": list(out.values()), "complete": complete, "expanded_via": expanded}


def diversification_metrics(fp: dict, hold: dict | None, fof_share: float = 0.0) -> dict:
    out = {}
    rows = sorted((hold or {}).get("holdings") or [], key=lambda r: -(r.get("weight") or 0))
    if rows and fof_share >= 0.5:
        why = (f"Fund of funds ({fof_share:.0%} in other funds): concentration of the wrapper is not meaningful — "
               "diversification comes from the underlying funds")
        out["top10_weight"] = M(None, reason=why)
        out["largest_holding"] = M(rows[0]["weight"], f"{rows[0].get('name') or rows[0].get('symbol')} (an underlying fund)")
        out["n_holdings"] = M(None, reason=why)
    elif rows:
        top10 = sum(r["weight"] for r in rows[:10])
        basis = "complete holdings" if hold.get("complete") else "top-10 list"
        out["top10_weight"] = M(top10, f"{basis}{', as of ' + hold['as_of'] if hold.get('as_of') else ''}")
        out["largest_holding"] = M(rows[0]["weight"], f"{rows[0].get('name') or rows[0].get('symbol')}")
        out["n_holdings"] = M(float(len(rows)), basis, unit="count") if hold.get("complete") else \
            M(None, reason="Only top-10 holdings available")
    else:
        out["top10_weight"] = M(None, reason="No holdings data")
        out["largest_holding"] = M(None, reason="No holdings data")
        out["n_holdings"] = M(None, reason="No holdings data")
    sw = fp.get("sector_weights") or {}
    out["max_sector_weight"] = M(max(sw.values()), f"largest sector: {max(sw, key=sw.get)}") if sw else M(None, reason="No sector data")
    ac = fp.get("asset_classes") or {}
    out["equity_share"] = M(ac.get("stockPosition"), "asset mix (vendor)") if ac.get("stockPosition") is not None else M(None)
    out["bond_share"] = M(ac.get("bondPosition"), "asset mix (vendor)") if ac.get("bondPosition") is not None else M(None)
    return out


def bond_metrics(fp: dict) -> dict:
    b = fp.get("bond") or {}
    out = {}
    out["duration"] = M(b.get("duration"), "effective duration (vendor)", unit="years") if b.get("duration") else \
        M(None, reason="Duration not reported — do not infer from yield")
    out["avg_maturity"] = M(b.get("maturity"), "average maturity (vendor)", unit="years") if b.get("maturity") else M(None, reason="Not reported")
    r = b.get("ratings") or {}
    if r:
        hq = sum(v for k, v in r.items() if k in ("aaa", "aa"))
        out["high_grade_share"] = M(hq, "AAA + AA share of rated bonds")
        out["below_ig_share"] = M(sum(v for k, v in r.items() if k in ("bb", "b", "below_b")), "below investment grade")
    else:
        out["high_grade_share"] = M(None, reason="No credit breakdown")
        out["below_ig_share"] = M(None, reason="No credit breakdown")
    y, d = fp.get("distribution_yield"), b.get("duration")
    out["yield_per_duration"] = M(y / d, "distribution yield / duration (crude risk-adjusted carry)", unit="x") if y and d else \
        M(None, reason="Needs yield and duration")
    return out


def tracking_metrics(ac: pd.Series, idx_local: pd.Series | None, label: str, years: int = 3) -> dict:
    if idx_local is None or idx_local.empty:
        return {"tracking_difference": M(None, reason="No index total-return series for this cohort"),
                "tracking_error": M(None, reason="No index total-return series for this cohort")}
    common = ac.index.intersection(idx_local.index)
    common = common[common >= common[-1] - pd.Timedelta(days=365 * years)] if len(common) else common
    if len(common) < 200:
        return {"tracking_difference": M(None, reason="Under one year of overlapping history"),
                "tracking_error": M(None, reason="Under one year of overlapping history")}
    f, i = ac.reindex(common), idx_local.reindex(common)
    yrs = (common[-1] - common[0]).days / 365.25
    fa, ia = (f.iloc[-1] / f.iloc[0]) ** (1 / yrs) - 1, (i.iloc[-1] / i.iloc[0]) ** (1 / yrs) - 1
    wf, wi = f.resample("W-FRI").last().pct_change().dropna(), i.resample("W-FRI").last().pct_change().dropna()
    w = wf.index.intersection(wi.index)
    te = float((wf.reindex(w) - wi.reindex(w)).std(ddof=1) * np.sqrt(TW)) if len(w) > 30 else None
    basis = f"vs {label}, {common[0].date()} → {common[-1].date()}"
    return {"tracking_difference": M(float(fa - ia), basis + " (annualised; incl. fees & withholding tax)"),
            "tracking_error": M(te, basis + " (weekly returns, annualised)") if te is not None else M(None, reason="Too few weeks")}


def look_through(hold: dict | None, stock_ey: dict[str, tuple[float | None, bool]], vendor: dict | None) -> dict:
    out = {}
    rows = (hold or {}).get("holdings") or []

    mapped = [(r["weight"], *stock_ey[r["symbol"]]) for r in rows if r.get("symbol") in stock_ey and stock_ey[r["symbol"]][0] is not None]
    mw = sum(w for w, _, _ in mapped)
    if mapped and mw > 0:
        ey = sum(w * e for w, e, _ in mapped) / mw
        loss = sum(w for w, _, prof in mapped if not prof) / mw
        # weights are fractions of the whole portfolio, so mapped weight IS the coverage
        status_ok = bool((hold or {}).get("complete")) and mw >= 0.8
        basis = (f"Σ w·EY over {len(mapped)} mapped holdings = {mw:.0%} of portfolio weight; loss-makers {loss:.0%} of mapped")
        out["lookthrough_earnings_yield"] = M(ey, basis + ("" if status_ok else " — PARTIAL (below 80% mapped)"))
        out["lookthrough_pe"] = M(1 / ey, basis, unit="x") if status_ok and ey > 0 else \
            M(None, status="nm", reason="Implied P/E needs ≥ 80% mapped weight from complete holdings")
        out["lookthrough_coverage"] = M(mw, "weight mapped to analysed stocks")
    else:
        out["lookthrough_earnings_yield"] = M(None, reason="Holdings not mapped to the analysed stock universe")
        out["lookthrough_pe"] = M(None, reason="Holdings not mapped to the analysed stock universe")
        out["lookthrough_coverage"] = M(0.0 if rows else None, "weight mapped to analysed stocks")
    v = (vendor or {}).get("earnings_yield")
    out["vendor_pe"] = M(1 / v, "Yahoo/Morningstar portfolio aggregate (reported as an inverse ratio; vendor convention)", unit="x") \
        if v and v > 0 else M(None, reason="No vendor aggregate")
    return out


def overlap(a: dict | None, b: dict | None) -> dict:
    """Σ min(weights) over common securities. Lower bound unless both holdings lists are complete."""
    wa = {r["symbol"]: r["weight"] for r in (a or {}).get("holdings") or [] if r.get("symbol")}
    wb = {r["symbol"]: r["weight"] for r in (b or {}).get("holdings") or [] if r.get("symbol")}
    if not wa or not wb:
        return {"overlap": None, "common": 0, "exact": False}
    common = set(wa) & set(wb)
    return {"overlap": float(sum(min(wa[k], wb[k]) for k in common)), "common": len(common),
            "exact": bool((a or {}).get("complete") and (b or {}).get("complete"))}
