"""Analysis run: stored snapshots -> metrics -> peer percentiles -> scores -> immutable published run."""
from __future__ import annotations

import logging
import traceback
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import funds as fund_analytics
from ..analytics import scoring
from ..analytics.fundamentals import M, compute_stock_fundamentals
from ..analytics.fx import FXTable
from ..analytics.market import (bars_frame, dividend_yield, liquidity_and_volume, relative_metrics, risk_metrics,
                                total_returns)
from ..config import get_settings
from ..db import AnalysisResult, AnalysisRun, Instrument, latest_completed_run, latest_snapshot, utcnow
from ..providers.canonical import normalise_quote_currency
from .ingest import MACRO
from .universe import BENCHMARKS, FINANCIAL_EXCLUSIONS

log = logging.getLogger(__name__)

ELIGIBILITY = {"min_market_cap_usd": 300e6, "min_traded_value_usd": 1e6, "min_history_sessions": 252}
SHORTLIST_THRESHOLD = 60.0
FINANCIALS_NA = ["fcf_yield", "fcf_ttm", "ev_ebitda", "ebitda_to_ev", "enterprise_value", "net_debt_ebitda",
                 "net_debt", "interest_coverage", "roic", "operating_margin", "gross_margin", "cash_conversion",
                 "payout_fcf", "fcf_growth"]
KEY_INPUTS = ["fcf_yield", "earnings_yield", "ebitda_to_ev", "roic", "operating_margin", "cash_conversion",
              "revenue_growth", "eps_growth", "net_debt_ebitda", "interest_coverage", "momentum_12_1",
              "volatility_1y", "return_1y"]
MODEL_VERSIONS = {"core": scoring.CORE_V1["id"], "estimate": scoring.ESTIMATE_V1["id"],
                  "attention": [scoring.ATTENTION_V1["id"], scoring.ATTENTION_PV_V1["id"]],
                  "formulas": "fundamentals_v1, market_v1"}


def clean_json(o):
    """NaN/inf -> None, numpy scalars -> python, dates -> iso (browser-safe JSON)."""
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if np.isnan(f) or np.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (date, datetime, pd.Timestamp)):
        return o.isoformat()
    return o


def _src(snap) -> dict | None:
    if snap is None:
        return None
    return {"provider": snap.provider, "route": snap.route, "status": snap.status,
            "fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None, "attempts": snap.attempts}


def _business_days_between(a: date, b: date) -> int:
    return max(0, int(np.busday_count(a, b)))


def _estimates_block(est: dict | None, fx_fin_px: float | None, price: float, trailing_eps: float | None) -> tuple[dict, dict]:
    metrics, summary = {}, {"periods": []}
    if not est:
        metrics["eps_revision"] = M(None, reason="No estimates")
        metrics["forward_pe"] = M(None, reason="No estimates")
        return metrics, summary
    periods = est.get("periods") or []
    summary = {"periods": periods, "basis": est.get("basis")}
    # revision: prefer next fiscal year, then current
    target = next((p for p in periods if p.get("period") in ("+1y",) and p.get("eps_trend")), None) or \
        next((p for p in periods if p.get("period") in ("0y",) and p.get("eps_trend")), None) or \
        next((p for p in periods if p.get("eps_trend")), None)
    if target:
        tr = target["eps_trend"]
        cur, d30, d90 = tr.get("current"), tr.get("d30"), tr.get("d90")
        n = target.get("eps_analysts") or 0
        if cur is not None and d30 and d90 and n >= 3:
            r30, r90 = (cur - d30) / abs(d30), (cur - d90) / abs(d90)
            metrics["eps_revision"] = M((r30 + r90) / 2, f"avg of 30d ({r30:+.2%}) & 90d ({r90:+.2%}) consensus change, "
                                                         f"same target period {target['period']}, {int(n)} analysts",
                                        formula="eps_revision_v1")
        else:
            metrics["eps_revision"] = M(None, reason="Needs ≥3 analysts and 30/90-day trend points")
    else:
        metrics["eps_revision"] = M(None, reason="Provider gives no estimate trend history yet (accumulating snapshots)")
    fwd = next((p for p in periods if p.get("period") in ("+1y",)), None) or (periods[1] if len(periods) > 1 else None)
    if fwd and fwd.get("eps_avg") and fx_fin_px:
        eps_px = fwd["eps_avg"] * fx_fin_px
        if trailing_eps and trailing_eps > 0 and not 0.2 <= fwd["eps_avg"] / trailing_eps <= 5:
            metrics["forward_pe"] = M(None, status="nm", reason="Estimate vs trailing EPS mismatch (currency/basis?) — suppressed")
        elif eps_px > 0:
            metrics["forward_pe"] = M(price / eps_px, f"price / next-FY consensus EPS ({fwd.get('period')})", unit="x")
        else:
            metrics["forward_pe"] = M(None, status="nm", reason="Negative forward EPS")
    else:
        metrics["forward_pe"] = M(None, reason="No forward EPS")
    return metrics, summary


def _targets_block(pt: dict | None, scale: float, price: float) -> dict:
    if not pt:
        return {"available": False}
    mid = pt.get("median") or pt.get("mean")
    out = {"available": True, "high": pt.get("high"), "low": pt.get("low"), "mean": pt.get("mean"),
           "median": pt.get("median"), "analysts": pt.get("analysts"), "scale": scale,
           "note": "External opinions; high–low range is not a confidence interval"}
    for k in ("high", "low", "mean", "median"):
        if out[k] is not None:
            out[k] = out[k] * scale
    if mid:
        m = mid * scale
        if not 0.2 <= m / price <= 5:
            out["upside"] = M(None, status="nm", reason="Target/price units inconsistent — suppressed")
        else:
            out["upside"] = M(m / price - 1, f"{'median' if pt.get('median') else 'mean'} target / price − 1")
            if out["high"] and out["low"]:
                out["dispersion"] = (out["high"] - out["low"]) / m
    return out


def _news_block(news: dict | None, provider: str | None) -> tuple[dict, list]:
    if not news:
        return M(None, reason="No news feed"), []
    seen, items = set(), []
    for it in news.get("items") or []:
        key = (it.get("title") or "").strip().lower()[:90]
        if key and key not in seen:
            seen.add(key)
            items.append(it)
    now = datetime.now(timezone.utc)

    def ts(it):
        v = it.get("published_at")
        try:
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v, timezone.utc)
            d = pd.Timestamp(v)
            return (d.tz_localize("UTC") if d.tzinfo is None else d).to_pydatetime()
        except Exception:
            return None
    dated = [(ts(i), i) for i in items]
    # count stories *about* the company (named in the headline) when there are enough of them
    heads = [(t, i) for t, i in dated if i.get("match", "headline") == "headline"]
    use, kind = (heads, "headline") if sum(1 for t, _ in heads if t and now - t <= timedelta(days=30)) >= 5 else (dated, "all")
    c7 = sum(1 for t, _ in use if t and now - t <= timedelta(days=7))
    c30 = sum(1 for t, _ in use if t and now - t <= timedelta(days=30))
    baseline = max((c30 - c7) * 7 / 23, 0.5)
    m = M(c7 / baseline, f"{c7} deduplicated {kind} stories in 7d vs trailing 23d baseline ({provider})", unit="x")
    if provider == "gdelt":
        m["basis"] += " — name-matched, lower confidence"
    items_sorted = sorted(items, key=lambda i: ts(i) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    items_sorted.sort(key=lambda i: i.get("match", "headline") != "headline")  # headline stories first
    return m, items_sorted[:25]


def build_subject(s: Session, inst: Instrument, fx: FXTable, cash: dict, bench: dict[str, pd.Series]) -> dict:
    snaps = {ds: latest_snapshot(s, inst.id, ds) for ds in
             ("profile", "prices", "financials", "estimates", "price_targets", "news")}
    prof = snaps["profile"].payload if snaps["profile"] else {}
    prices = snaps["prices"].payload if snaps["prices"] else {}
    fin = snaps["financials"].payload if snaps["financials"] else None
    flags, metrics = [], {}
    raw_ccy = prof.get("quote_currency") or prices.get("quote_currency") or inst.currency
    px_ccy, scale = normalise_quote_currency(raw_ccy)
    if scale != 1.0:
        flags.append(f"Quote in {raw_ccy} normalised to {px_ccy} (×{scale})")
    out = {"id": inst.id, "symbol": inst.symbol, "name": inst.name or prof.get("name"), "asset_type": inst.asset_type,
           "region": inst.region, "exchange": inst.exchange, "sector": inst.sector, "industry": inst.industry,
           "country": inst.country, "currency": px_ccy, "quote_currency_raw": raw_ccy,
           "description": prof.get("description"), "website": prof.get("website"), "employees": prof.get("employees"),
           "sources": {k: _src(v) for k, v in snaps.items()}, "flags": flags}

    df, pflags = bars_frame(prices, inst.region or "US", scale) if prices else (pd.DataFrame(), ["no prices"])
    session_note = next((f for f in pflags if f.startswith("Dropped today")), None)
    flags.extend(f for f in pflags if f != session_note)
    if df.empty:
        out.update({"status": "failed", "metrics": {}, "eligibility": {"core": False, "reasons": ["No price data"]}})
        return out
    price, price_date = float(df["c"].iloc[-1]), df.index[-1].date()
    out["price"] = {"value": price, "currency": px_ccy, "date": price_date.isoformat(),
                    "change_1d": float(df["c"].iloc[-1] / df["c"].iloc[-2] - 1) if len(df) > 1 else None,
                    "adjustment": prices.get("adjustment"),
                    "note": session_note or "Last completed session (official end-of-day basis)"}
    # market value
    shares = prof.get("implied_shares_outstanding") or prof.get("shares_outstanding")
    mcap = price * shares if shares else prof.get("market_cap")
    if prof.get("market_cap") and mcap and abs(prof["market_cap"] / mcap - 1) > 0.10:
        flags.append(f"Vendor market cap differs from price × shares by {prof['market_cap'] / mcap - 1:+.0%}")
    usd = fx.rate(px_ccy, "USD")
    pres = get_settings().presentation_currency
    out["market_cap"] = {"value": mcap, "currency": px_ccy, "usd": mcap * usd if mcap and usd else None,
                         "presentation": mcap * fx.rate(px_ccy, pres) if mcap and fx.rate(px_ccy, pres) else None,
                         "presentation_currency": pres, "basis": "price × shares outstanding (incl. all classes)"}

    # fundamentals
    fin_ccy = None
    if fin:
        fin_ccy, fscale = normalise_quote_currency(fin.get("financial_currency"))
        fx_fp = fx.rate(fin_ccy, px_ccy)
        fx_fp = fx_fp * fscale if fx_fp else None
        if fin_ccy != px_ccy:
            flags.append(f"Reports in {fin_ccy}, trades in {px_ccy}: statements converted at reference FX")
        metrics.update(compute_stock_fundamentals(fin, price, mcap, fx_fp))
    else:
        fx_fp = None
        flags.append("No financial statements from any provider")
    anchors = metrics.pop("_anchors", None)
    profitable = metrics.pop("_profitable", None)
    latest_period = metrics.pop("_latest_period", None)

    # market metrics
    ac = df["ac"]
    metrics.update(total_returns(ac))
    cash_series = cash.get(px_ccy)
    metrics.update(risk_metrics(ac, cash_series["points"] if cash_series else None,
                                cash_series["source"] if cash_series else None))
    b_sym = BENCHMARKS.get(inst.region or "US", BENCHMARKS["US"])
    metrics.update(relative_metrics(ac, bench.get(inst.region or "US"), b_sym[0]))
    metrics.update(liquidity_and_volume(df, usd))
    metrics["dividend_yield"] = dividend_yield(prices, scale, price, df.index[-1])

    # estimates, targets, news
    trailing_eps = None
    if fin:
        from ..analytics.fundamentals import Statements
        trailing_eps = Statements(fin).ttm("eps_diluted")[0]
    em, est_summary = _estimates_block(snaps["estimates"].payload if snaps["estimates"] else None, fx_fp, price, trailing_eps)
    metrics.update(em)
    targets = _targets_block(snaps["price_targets"].payload if snaps["price_targets"] else None, scale, price)
    if targets.get("upside"):
        metrics["target_upside"] = targets["upside"]
    news_metric, news_items = _news_block(snaps["news"].payload if snaps["news"] else None,
                                          snaps["news"].provider if snaps["news"] else None)
    metrics["news_activity"] = news_metric

    # freshness (trading sessions; statements by expected cadence — spec §14)
    age = _business_days_between(price_date, date.today())
    quarterly_reporter = bool(fin and fin.get("quarterly"))
    max_age = 200 if quarterly_reporter else 290  # quarter + filing lag vs half-year + lag
    fresh = {"price_sessions_old": age, "price_stale": age > 2, "latest_statement": latest_period,
             "reporting_cadence": "quarterly" if quarterly_reporter else "semi-annual/annual",
             "statement_stale": bool(latest_period and (date.today() - date.fromisoformat(latest_period)).days > max_age)}
    if fresh["price_stale"]:
        flags.append(f"Prices are {age} sessions old")
    if fresh["statement_stale"]:
        flags.append(f"Latest statement period {latest_period} — expected update may be missing")
    if fin and inst.region != "US" and metrics.get("fcf_yield", {}).get("status") == "valid":
        metrics["fcf_yield"]["basis"] += " — IFRS 16: lease repayments sit in financing, FCF may be overstated"

    # applicability / eligibility (spec §4 step 3, §6)
    reasons = []
    ind = f"{inst.industry or ''} {inst.sector or ''}".lower()
    model_family = "general"
    if any(x in (inst.industry or "").lower() for x in FINANCIAL_EXCLUSIONS):
        model_family = "financials"
        reasons.append(f"{inst.industry}: bank/insurer/REIT model not yet validated — metrics shown, no composite")
        # Operating-company metrics are not meaningful for balance-sheet businesses (spec §5)
        for k in FINANCIALS_NA:
            if k in metrics:
                metrics[k] = M(None, status="na", reason="Not applicable to banks/insurers (deposits & float are not "
                                                          "operating debt; capex/FCF not comparable)")
    elif "reit" in ind:
        model_family = "reit"
        reasons.append("REIT model not yet validated")
    if profitable is False:
        model_family = "loss_making" if model_family == "general" else model_family
        reasons.append("Loss-making on TTM basis — separate model pending")
    mc_usd = out["market_cap"]["usd"]
    if not mc_usd or mc_usd < ELIGIBILITY["min_market_cap_usd"]:
        reasons.append("Market value below USD 300M or unknown")
    tv = metrics.get("median_traded_value_usd_60d", {}).get("value")
    if not tv or tv < ELIGIBILITY["min_traded_value_usd"]:
        reasons.append("60-session median traded value below USD 1M")
    if len(df) < ELIGIBILITY["min_history_sessions"]:
        reasons.append("Less than 252 sessions of price history")
    if not fin:
        reasons.append("No valid financial statements")
    if inst.asset_type != "stock":
        reasons.append(f"{inst.asset_type}: stock model not applicable")
    out["eligibility"] = {"core": not reasons, "model_family": model_family, "reasons": reasons, "rules": ELIGIBILITY}

    applicable = [k for k in KEY_INPUTS if not (model_family == "financials" and k in FINANCIALS_NA)]
    valid = sum(1 for k in applicable if metrics.get(k, {}).get("status") == "valid")
    conf = valid / len(applicable) * (0.8 if fresh["price_stale"] or fresh["statement_stale"] else 1.0)
    unofficial = any((v or {}).get("provider") in ("yahoo", "gdelt") for v in out["sources"].values())
    out["confidence"] = {"score": round(conf * 100), "label": "High" if conf >= 0.8 else "Medium" if conf >= 0.6 else "Low",
                         "valid_inputs": valid, "total_inputs": len(applicable), "freshness": fresh,
                         "unofficial_source": unofficial,
                         "note": "Input completeness & freshness — not confidence of profit"}
    out.update({"status": "ok", "metrics": metrics, "anchors": anchors, "financial_currency": fin_ccy,
                "estimates": est_summary, "targets": {k: v for k, v in targets.items() if k != "upside"},
                "news": news_items, "fx_fin_to_px": fx_fp})
    return out


# ============================================================================================ funds
def build_fund_subject(s: Session, inst: Instrument, fx: FXTable, cash: dict, indices: dict[str, pd.Series],
                       stock_ey: dict[str, tuple[float | None, bool]], fund_holdings: dict[str, dict] | None = None) -> dict:
    snaps = {ds: latest_snapshot(s, inst.id, ds) for ds in ("profile", "prices", "fund_profile", "holdings")}
    prof = snaps["profile"].payload if snaps["profile"] else {}
    prices = snaps["prices"].payload if snaps["prices"] else {}
    fp = snaps["fund_profile"].payload if snaps["fund_profile"] else {}
    hold = snaps["holdings"].payload if snaps["holdings"] else None
    cat = (inst.tags or {}).get("fund") or {}
    raw_ccy = prof.get("quote_currency") or fp.get("currency") or inst.currency
    px_ccy, scale = normalise_quote_currency(raw_ccy)
    flags = [f"Quote in {raw_ccy} normalised to {px_ccy} (×{scale})"] if scale != 1.0 else []
    out = {"id": inst.id, "symbol": inst.symbol, "name": fp.get("name") or inst.name or prof.get("name"),
           "asset_type": inst.asset_type, "asset_class": "fund", "region": inst.region, "exchange": inst.exchange,
           "currency": px_ccy, "quote_currency_raw": raw_ccy, "family": fp.get("family"),
           "category": fp.get("category"), "cohort": cat.get("cohort") or fp.get("category") or "Uncategorised",
           "fund_asset_class": cat.get("asset_class") or _infer_asset_class(fp), "style": cat.get("style", "passive"),
           "hedged": cat.get("hedged", False), "index": cat.get("index"),
           "sources": {k: _src(v) for k, v in snaps.items()}, "flags": flags, "description": prof.get("description")}
    df, pflags = bars_frame(prices, inst.region or "US", scale) if prices else (pd.DataFrame(), ["no prices"])
    session_note = next((f for f in pflags if f.startswith("Dropped today")), None)
    # mutual funds price once a day at NAV and have no exchange volume — volume checks do not apply
    flags.extend(f for f in pflags if f != session_note and not (inst.asset_type == "mutualfund" and "zero-volume" in f))
    if df.empty:
        out.update({"status": "failed", "metrics": {}, "eligibility": {"core": False, "reasons": ["No price data"]}})
        return out
    price = float(df["c"].iloc[-1])
    out["price"] = {"value": price, "currency": px_ccy, "date": df.index[-1].date().isoformat(),
                    "change_1d": float(df["c"].iloc[-1] / df["c"].iloc[-2] - 1) if len(df) > 1 else None,
                    "adjustment": prices.get("adjustment"), "note": session_note or "Last completed session"}
    m: dict = {}
    m.update(fund_analytics.cost_metrics(fp))
    aum = fp.get("aum")
    aum_usd = aum * fx.rate(px_ccy, "USD") if aum and fx.rate(px_ccy, "USD") else None
    m.update(fund_analytics.scale_metrics(fp, aum_usd))
    fund_holdings = fund_holdings or {}
    fof = fund_analytics.fund_share(hold, set(fund_holdings))
    look = fund_analytics.expand_holdings(hold, fund_holdings) if fof > 0 else hold
    if fof >= 0.5:
        flags.append(f"Fund of funds: {fof:.0%} held in other funds{(' (' + ', '.join(look.get('expanded_via') or []) + ')') if look and look.get('expanded_via') else ''}"
                     " — look-through used for valuation and overlap")
    unexpanded = fund_analytics.fund_share(look, set(fund_holdings)) if look else 1.0
    if fof >= 0.5 and look and look.get("expanded_via") and unexpanded < 0.2:
        # concentration measured on the look-through portfolio (top-10 lists of the underlying funds)
        m.update(fund_analytics.diversification_metrics(fp, {**look, "complete": False}, 0.0))
        m["top10_weight"]["basis"] = f"look-through via {', '.join(look['expanded_via'])} (lower bound)"
    else:
        m.update(fund_analytics.diversification_metrics(fp, hold, fof))
    ac = df["ac"]
    m.update(total_returns(ac))
    cs = cash.get(px_ccy)
    m.update(risk_metrics(ac, cs["points"] if cs else None, cs["source"] if cs else None))
    if out["fund_asset_class"] == "fixed_income":
        m.update(fund_analytics.bond_metrics(fp))
    # tracking vs total-return index in the fund's economic currency
    idx = indices.get(out["index"]) if out["index"] else None
    if idx is not None:
        idx_ccy = "USD"
        if out["hedged"] or px_ccy == idx_ccy:
            idx_local, lbl = idx, f"{out['index']} ({idx_ccy}{', hedged fund compared in index currency' if out['hedged'] else ''})"
        else:
            conv = fx.series_rate(idx_ccy, px_ccy, idx.index)
            idx_local, lbl = (idx * conv if conv is not None else None), f"{out['index']} converted {idx_ccy}→{px_ccy} at reference FX"
        m.update(fund_analytics.tracking_metrics(ac, idx_local, lbl))
    else:
        m.update(fund_analytics.tracking_metrics(ac, None, ""))
    m.update(fund_analytics.look_through(look, stock_ey, fp.get("vendor_valuation")))
    out["price"]["nav"] = fp.get("nav")
    m["premium_discount"] = M(None, reason="Conditional: NAV and price timestamps not matched by any provider in the chain")
    # model applicability (spec §7)
    reasons = []
    if out["fund_asset_class"] == "fixed_income":
        model = None
        reasons.append("Fixed-income funds: category dashboard only until a validated bond-fund rank exists (duration must not win on yield)")
    elif out["style"] == "active":
        model = None
        reasons.append("Active fund: cost, benchmark-relative return, drawdown and concentration shown separately; composite deferred")
    elif m.get("tracking_difference", {}).get("status") == "valid":
        model = "passive_equity_v1"
    else:
        model = "basic_fund_screen_v1"
    out["fund_model"] = model
    out["eligibility"] = {"core": model is not None, "model_family": out["fund_asset_class"], "reasons": reasons}
    out["holdings"] = {"complete": (hold or {}).get("complete", False), "as_of": (hold or {}).get("as_of"),
                       "covered_weight": (hold or {}).get("covered_weight"),
                       "top": sorted((hold or {}).get("holdings") or [], key=lambda r: -(r.get("weight") or 0))[:25],
                       "count": len((hold or {}).get("holdings") or [])}
    out["exposure"] = {"sectors": fp.get("sector_weights") or {}, "asset_classes": fp.get("asset_classes") or {},
                       "bond": fp.get("bond") or {}, "vendor_valuation": fp.get("vendor_valuation") or {}}
    key = ["expense_ratio", "aum_usd", "top10_weight", "return_1y", "volatility_1y", "max_drawdown_3y"]
    valid = sum(1 for k in key if m.get(k, {}).get("status") == "valid")
    age = _business_days_between(df.index[-1].date(), date.today())
    conf = valid / len(key) * (0.8 if age > 2 else 1.0)
    out["confidence"] = {"score": round(conf * 100), "label": "High" if conf >= 0.8 else "Medium" if conf >= 0.6 else "Low",
                         "valid_inputs": valid, "total_inputs": len(key), "unofficial_source": True,
                         "freshness": {"price_sessions_old": age, "price_stale": age > 2, "latest_statement": None,
                                       "statement_stale": False, "reporting_cadence": "n/a",
                                       "holdings_as_of": (hold or {}).get("as_of")},
                         "note": "Input completeness & freshness — not confidence of profit"}
    out.update({"status": "ok", "metrics": m})
    return out


def _infer_asset_class(fp: dict) -> str:
    ac = fp.get("asset_classes") or {}
    st, bd = ac.get("stockPosition") or 0, ac.get("bondPosition") or 0
    return "fixed_income" if bd > 0.7 else "equity" if st > 0.7 else "allocation"


# ============================================================================================ rows
ROW_METRICS_STOCK = ["pe", "forward_pe", "ev_ebitda", "fcf_yield", "earnings_yield", "roic", "operating_margin",
                     "gross_margin", "revenue_growth", "eps_growth", "net_debt_ebitda", "interest_coverage",
                     "dividend_yield", "return_1y", "return_ytd", "return_3y_ann", "volatility_1y", "max_drawdown_1y",
                     "sharpe_1y", "momentum_12_1", "target_upside", "eps_revision", "beta_1y", "relative_return_6m",
                     "payout_fcf", "cash_conversion"]
ROW_METRICS_FUND = ["expense_ratio", "aum_usd", "top10_weight", "n_holdings", "tracking_difference", "tracking_error",
                    "return_1y", "return_3y_ann", "return_5y_ann", "volatility_1y", "max_drawdown_3y", "sharpe_1y",
                    "distribution_yield", "duration", "high_grade_share", "lookthrough_pe", "vendor_pe", "equity_share",
                    "bond_share", "max_sector_weight", "age_years", "cost_vs_category"]


def make_row(p: dict) -> dict:
    sc = p.get("scores") or {}
    core = sc.get("core") or {}
    m = p.get("metrics") or {}
    is_fund = p.get("asset_class") == "fund"
    keys = ROW_METRICS_FUND if is_fund else ROW_METRICS_STOCK
    row = {
        "id": p["id"], "symbol": p["symbol"], "name": p.get("name"), "region": p.get("region"),
        "exchange": p.get("exchange"), "currency": p.get("currency"), "sector": p.get("sector"),
        "industry": p.get("industry"), "asset_type": p.get("asset_type"), "asset_class": p.get("asset_class", "stock"),
        "status": p.get("status"), "price": p.get("price"), "market_cap": p.get("market_cap"),
        "core": {k: core.get(k) for k in ("score", "rank", "status", "coverage", "reasons", "model", "label")},
        "estimate": {k: (sc.get("estimate") or {}).get(k) for k in ("score", "rank", "status")},
        "attention": {k: (sc.get("attention") or {}).get(k) for k in ("score", "model", "status")},
        "components": {c: v.get("score") for c, v in (core.get("components") or {}).items()},
        "metrics": {k: {"value": (m.get(k) or {}).get("value"), "status": (m.get(k) or {}).get("status", "na")} for k in keys},
        "confidence": p.get("confidence"), "eligibility": p.get("eligibility"), "flags": p.get("flags") or [],
        "discovery": p.get("discovery") or [], "indices": p.get("indices") or [],
    }
    if is_fund:
        row.update({k: p.get(k) for k in ("cohort", "fund_asset_class", "style", "family", "hedged", "index", "fund_model")})
    return row


# ============================================================================================ run
DISCOVERY_LISTS = [  # spec §4 step 5: union of lists so one theme cannot monopolise discovery
    ("Composite leader", lambda v: (v["scores"].get("core") or {}).get("score"), 0.90),
    ("Attractive valuation", lambda v: _comp(v, "value"), 0.90),
    ("High quality", lambda v: _comp(v, "quality"), 0.90),
    ("Strong growth", lambda v: _comp(v, "growth"), 0.90),
    ("Rising estimates", lambda v: _metric(v, "eps_revision"), 0.90),
    ("Unusual attention", lambda v: (v["scores"].get("attention") or {}).get("score"), 0.95),
]


def _comp(v, k):
    return (((v.get("scores") or {}).get("core") or {}).get("components") or {}).get(k, {}).get("score")


def _metric(v, k):
    m = (v.get("metrics") or {}).get(k) or {}
    return m.get("value") if m.get("status") == "valid" else None


def tag_discovery(subjects: dict[str, dict]) -> dict[str, int]:
    counts = {}
    for label, fn, q in DISCOVERY_LISTS:
        vals = {sid: fn(v) for sid, v in subjects.items() if v.get("status") == "ok" and v.get("scores") and fn(v) is not None}
        if len(vals) < 5:
            continue
        cut = float(np.quantile(list(vals.values()), q))
        hits = [sid for sid, x in vals.items() if x >= cut and (label != "Rising estimates" or x > 0)]
        counts[label] = len(hits)
        for sid in hits:
            subjects[sid].setdefault("discovery", []).append(label)
    return counts


def compute_run(s: Session, run_id: str | None = None) -> AnalysisRun:
    # Workers update instruments (sector, name, type) in their own sessions; drop this session's cached
    # copies so the run sees current values rather than what was loaded at job start.
    s.expire_all()
    run = AnalysisRun(model_versions=MODEL_VERSIONS)
    if run_id:
        run.id = run_id
    s.add(run)
    s.commit()
    try:
        fx_snap = latest_snapshot(s, MACRO, "fx")
        fx = FXTable(fx_snap.payload if fx_snap else None, fx_snap.provider if fx_snap else None)
        cash_snap = latest_snapshot(s, MACRO, "cash_rates")
        cash = cash_snap.payload if cash_snap else {}

        insts = s.scalars(select(Instrument).where(Instrument.active.is_(True))).all()
        bench: dict[str, pd.Series] = {}
        indices: dict[str, pd.Series] = {}
        for b in insts:
            region_for = (b.tags or {}).get("benchmark_for")
            if not region_for and b.asset_type != "index":
                continue
            ps = latest_snapshot(s, b.id, "prices")
            if not ps:
                continue
            _, sc_ = normalise_quote_currency((ps.payload or {}).get("quote_currency") or b.currency)
            bdf, _ = bars_frame(ps.payload, b.region or "US", sc_)
            if bdf.empty:
                continue
            if region_for:
                bench[region_for] = bdf["ac"]
            if b.asset_type == "index":
                indices[b.symbol] = bdf["ac"]

        stocks = [i for i in insts if i.asset_type == "stock"]
        funds = [i for i in insts if i.asset_type in ("etf", "mutualfund", "fund")]
        subjects: dict[str, dict] = {}
        failed = 0
        for inst in stocks:
            try:
                subj = build_subject(s, inst, fx, cash, bench)
                subj["asset_class"] = "stock"
                subj["indices"] = (inst.tags or {}).get("indices") or []
                subjects[inst.id] = subj
            except Exception as e:  # one bad instrument must not sink the run
                failed += 1
                log.exception("analysis failed for %s", inst.symbol)
                subjects[inst.id] = {"id": inst.id, "symbol": inst.symbol, "name": inst.name, "status": "failed",
                                     "asset_class": "stock", "error": f"{type(e).__name__}: {e}", "metrics": {},
                                     "eligibility": {"core": False, "reasons": ["Calculation error"]}}

        # ---------------- stock scoring
        min_size = get_settings().peer_min_size
        ok = {k: v for k, v in subjects.items() if v.get("status") == "ok"}
        cohort = {k: v for k, v in ok.items() if v["eligibility"]["core"]}
        core_p = scoring.peer_percentiles(cohort, scoring.all_metric_keys(scoring.CORE_V1), min_size)
        est_p = scoring.peer_percentiles(cohort, scoring.all_metric_keys(scoring.ESTIMATE_V1), min_size)
        news_cov = np.mean([v["metrics"].get("news_activity", {}).get("status") == "valid" for v in ok.values()]) if ok else 0
        att_model = scoring.ATTENTION_V1 if news_cov >= 0.8 else scoring.ATTENTION_PV_V1
        att_p = scoring.peer_percentiles(ok, scoring.all_metric_keys(att_model), min_size)
        level_counts: dict[str, int] = {}
        for sid, subj in subjects.items():
            if subj.get("status") != "ok":  # every unscored row must say why
                subj["scores"] = {"core": {"model": "core_v1", "score": None, "status": "no_data",
                                           "reasons": (subj.get("eligibility") or {}).get("reasons") or ["No usable data"]}}
                continue
            sc = {}
            if sid in cohort:
                sc["core"] = scoring.score_model(scoring.CORE_V1, subj["metrics"], core_p.get(sid, {}))
                sc["estimate"] = scoring.score_model(scoring.ESTIMATE_V1, subj["metrics"], est_p.get(sid, {}))
                for p_ in core_p.get(sid, {}).values():
                    level_counts[p_["level"]] = level_counts.get(p_["level"], 0) + 1
            else:
                sc["core"] = {"model": "core_v1", "score": None, "status": "not_applicable",
                              "reasons": subj["eligibility"]["reasons"]}
                sc["estimate"] = dict(sc["core"], model="estimate_enhanced_v1")
            sc["attention"] = scoring.score_attention(att_model, att_p.get(sid, {}))
            subj["scores"] = sc
        for key in ("core", "estimate"):
            ranked = sorted([v for v in subjects.values() if (v.get("scores") or {}).get(key, {}).get("score") is not None],
                            key=lambda v: (-v["scores"][key]["score"], v["symbol"]))
            for i, v in enumerate(ranked, 1):
                v["scores"][key]["rank"] = i
        scored_core = sum(1 for v in subjects.values() if (v.get("scores") or {}).get("core", {}).get("score") is not None)
        disc_counts = tag_discovery({k: v for k, v in subjects.items() if v.get("asset_class") == "stock"})

        # ---------------- funds (need stock earnings yields for look-through)
        stock_ey = {v["symbol"]: (_metric(v, "earnings_yield"), (_metric(v, "earnings_yield") or 0) > 0)
                    for v in subjects.values() if v.get("status") == "ok"}
        fund_holdings = {}
        for inst in funds:
            hs = latest_snapshot(s, inst.id, "holdings")
            if hs and hs.payload:
                fund_holdings[inst.symbol] = hs.payload
        fund_subj: dict[str, dict] = {}
        for inst in funds:
            try:
                fund_subj[inst.id] = build_fund_subject(s, inst, fx, cash, indices, stock_ey, fund_holdings)
            except Exception as e:  # noqa: BLE001
                failed += 1
                log.exception("fund analysis failed for %s", inst.symbol)
                fund_subj[inst.id] = {"id": inst.id, "symbol": inst.symbol, "name": inst.name, "status": "failed",
                                      "asset_class": "fund", "error": f"{type(e).__name__}: {e}", "metrics": {},
                                      "eligibility": {"core": False, "reasons": ["Calculation error"]}}
        fok = {k: v for k, v in fund_subj.items() if v.get("status") == "ok"}
        for v in fok.values():
            v["family"] = f"{v['fund_asset_class']} · {v['style']}"  # broader peer level for small cohorts
        fund_min = 3
        for model in (scoring.PASSIVE_EQUITY_V1, scoring.BASIC_FUND_SCREEN_V1):
            members = {k: v for k, v in fok.items() if v.get("fund_model") == model["id"]}
            fp_ = scoring.peer_percentiles(members, scoring.all_metric_keys(model), fund_min, levels=scoring.FUND_PEER_LEVELS)
            for sid, v in members.items():
                v["scores"] = {"core": scoring.score_model(model, v["metrics"], fp_.get(sid, {}))}
        for sid, v in fund_subj.items():
            if "scores" not in v:
                v["scores"] = {"core": {"model": v.get("fund_model"), "score": None, "status": "not_applicable",
                                        "reasons": (v.get("eligibility") or {}).get("reasons") or []}}
        for cohort_name in {v.get("cohort") for v in fok.values()}:
            ranked = sorted([v for v in fok.values() if v.get("cohort") == cohort_name and v["scores"]["core"].get("score") is not None],
                            key=lambda v: (-v["scores"]["core"]["score"], v["symbol"]))
            for i, v in enumerate(ranked, 1):
                v["scores"]["core"]["rank"] = i
                v["scores"]["core"]["rank_of"] = len(ranked)
        subjects.update(fund_subj)

        for sid, subj in subjects.items():
            payload = clean_json(subj)
            s.add(AnalysisResult(run_id=run.id, instrument_id=sid, payload=payload, row=make_row(payload),
                                 asset_class=subj.get("asset_class", "stock"),
                                 core_score=(subj.get("scores") or {}).get("core", {}).get("score")))
        cohort_regions = pd.Series([v["region"] for v in cohort.values()]).value_counts().to_dict() if cohort else {}
        below = level_counts.get("universe", 0)
        run.manifest = {
            "evaluated": len(subjects), "ok": len(ok) + len(fok), "failed": failed,
            "stocks": len(stocks), "funds": len(funds), "eligible_core": len(cohort),
            "excluded": len(ok) - len(cohort), "scored_core": scored_core, "cohort_by_region": cohort_regions,
            "peer_min_size": min_size, "peer_levels_used": level_counts,
            "peer_note": (f"Cohort of {len(cohort)} is below the {min_size}-member peer minimum: percentiles use the "
                          f"broadest fallback (whole eligible universe). Treat ranks as indicative.")
            if len(cohort) < min_size else (f"{below} metric percentiles fell back to the whole universe (small sector/region groups)." if below else ""),
            "attention_model": att_model["id"], "news_coverage": round(float(news_cov), 2),
            "discovery_lists": disc_counts,
            "fund_models": {m_: sum(1 for v in fok.values() if v.get("fund_model") == m_) for m_ in ("passive_equity_v1", "basic_fund_screen_v1", None)},
            "fx": {"provider": fx.source, "as_of": fx.as_of("USD"),
                   "USDCAD": fx.rate("USD", "CAD"), "GBPCAD": fx.rate("GBP", "CAD"), "EURCAD": fx.rate("EUR", "CAD")},
            "cash_rates": {k: {"source": v["source"], "latest": v["points"][-1]} for k, v in cash.items()},
            "benchmarks": {r: b[0] for r, b in BENCHMARKS.items() if r in bench},
            "indices": list(indices), "shortlist_threshold": SHORTLIST_THRESHOLD,
        }
        # quality gate (spec §4 step 7): refuse to publish if coverage collapsed vs the previous run
        prev = latest_completed_run(s)
        prev_ok = (prev.manifest or {}).get("ok") if prev else None
        if prev_ok and run.manifest["ok"] < 0.6 * prev_ok:
            run.status = "failed"
            run.manifest["error"] = (f"Quality gate: only {run.manifest['ok']} instruments analysed vs {prev_ok} in the last "
                                     f"published run — previous snapshot kept")
        else:
            run.status = "completed"
        s.commit()
        if run.status == "completed":
            from .alerts import generate_alerts
            generate_alerts(s, run, prev)
    except Exception:
        log.exception("analysis run failed")
        s.rollback()
        run.status = "failed"
        run.manifest = {"error": traceback.format_exc()[-2000:]}
        s.commit()
    return run
