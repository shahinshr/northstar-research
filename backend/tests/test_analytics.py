"""Calculation tests mirroring spec §20 acceptance cases (deterministic, no network)."""
import math

import pandas as pd

from app.analytics import scoring
from app.analytics.fundamentals import Statements, compute_stock_fundamentals
from app.analytics.fx import FXTable
from app.analytics.market import risk_metrics
from app.providers.canonical import clean_fields, normalise_quote_currency, num


def _q(end, **f):
    return {"period_end": end, "fiscal_period": "Q", "filing_date": None, "fields": f}


def _a(end, **f):
    return {"period_end": end, "fiscal_period": "FY", "filing_date": None, "fields": f}


def test_spec_worked_example_core_score_71():
    # spec §6: component scores 80,70,65,60,75 -> 71.0
    comps = {"value": 80, "quality": 70, "growth": 65, "resilience": 60, "momentum": 75}
    total = sum(scoring.CORE_V1["components"][k]["weight"] * v for k, v in comps.items())
    assert round(total, 1) == 71.0


def test_gbx_normalisation():
    assert normalise_quote_currency("GBp") == ("GBP", 0.01)
    assert normalise_quote_currency("GBX") == ("GBP", 0.01)
    assert normalise_quote_currency("usd") == ("USD", 1.0)


def test_missing_is_never_zero():
    assert num(None) is None and num("None") is None and num(float("nan")) is None and num("") is None
    assert clean_fields({"capex": -50, "revenue": None}) == {"capex": 50}


def test_negative_eps_is_nm_not_bargain():
    fin = {"financial_currency": "USD", "quarterly": [],
           "annual": [_a("2025-12-31", revenue=100, net_income=-10, eps_diluted=-1.0, operating_income=-5,
                         ebitda=-2, ebit=-5, total_debt=50, cash=10, total_equity=40, operating_cash_flow=5, capex=8),
                      _a("2024-12-31", revenue=90, net_income=-20, eps_diluted=-2.0)]}
    m = compute_stock_fundamentals(fin, price=10, market_cap=1000, fx_fin_to_px=1.0)
    assert m["pe"]["status"] == "nm"
    assert m["ev_ebitda"]["status"] == "nm"
    assert m["net_debt_ebitda"]["status"] == "nm"
    assert m["eps_growth"]["status"] == "nm"  # negative base
    assert m["cash_conversion"]["status"] == "nm"


def test_ttm_sums_four_consecutive_quarters_only():
    fin = {"financial_currency": "USD", "annual": [_a("2025-12-31", revenue=300)],
           "quarterly": [_q("2026-09-30", revenue=110), _q("2026-06-30", revenue=100),
                         _q("2026-03-31", revenue=90), _q("2025-12-31", revenue=80)]}
    v, basis = Statements(fin).ttm("revenue")
    assert v == 380 and basis.startswith("TTM")
    fin["quarterly"].pop(2)  # gap -> must fall back to FY rather than sum non-consecutive quarters
    v, basis = Statements(fin).ttm("revenue")
    assert v == 300 and basis.startswith("FY")


def test_currency_conversion_applied_to_statements():
    fin = {"financial_currency": "USD", "quarterly": [],
           "annual": [_a("2025-12-31", revenue=100, net_income=10, eps_diluted=1.0, operating_cash_flow=20, capex=5)]}
    m = compute_stock_fundamentals(fin, price=20, market_cap=2000, fx_fin_to_px=1.4)  # USD->CAD
    assert math.isclose(m["earnings_yield"]["value"], 1.4 / 20)
    assert math.isclose(m["fcf_yield"]["value"], 15 * 1.4 / 2000)


def test_fx_cross_rates():
    fx = FXTable({"series": {"USD": [{"d": "2026-11-04", "v": 1.40}], "GBP": [{"d": "2026-11-04", "v": 1.82}]}})
    assert math.isclose(fx.rate("GBP", "USD"), 1.82 / 1.40)
    assert fx.rate("CAD", "CAD") == 1.0 and fx.rate("EUR", "CAD") is None


def test_percentiles_midrank_and_direction():
    p = scoring.percentile_ranks({"a": 1, "b": 2, "c": 2, "d": 3}, "higher")
    assert p["b"] == p["c"]  # ties share mid-rank
    assert p["a"] < p["b"] < p["d"]
    q = scoring.percentile_ranks({"a": 1, "b": 3}, "lower")
    assert q["a"] > q["b"]


def test_missing_data_rules_block_composite():
    # momentum input missing -> component has 0% < 50% -> no composite (Insufficient data)
    pcts = {k: {"pct": 50, "level": "universe", "n": 5} for k, _ in scoring.all_metric_keys(scoring.CORE_V1)}
    pcts.pop("momentum_12_1")
    r = scoring.score_model(scoring.CORE_V1, {}, pcts)
    assert r["score"] is None and r["status"] == "insufficient_data"
    # interest coverage missing only -> resilience keeps 60% >= 50%, total coverage 94% -> scored
    pcts = {k: {"pct": 50, "level": "universe", "n": 5} for k, _ in scoring.all_metric_keys(scoring.CORE_V1)}
    pcts.pop("interest_coverage")
    r = scoring.score_model(scoring.CORE_V1, {}, pcts)
    assert r["score"] == 50.0 and math.isclose(r["coverage"], 0.94)


def test_deterministic_replay():
    vals = {"x": 0.1, "y": 0.3, "z": 0.2}
    assert scoring.percentile_ranks(vals, "higher") == scoring.percentile_ranks(dict(reversed(vals.items())), "higher")


def test_sharpe_suppressed_without_cash_rate():
    idx = pd.bdate_range("2024-01-01", periods=300)
    ac = pd.Series([100 * (1.0005 ** i) for i in range(300)], index=idx)
    out = risk_metrics(ac, None, None)
    assert out["sharpe_1y"]["value"] is None
    assert out["volatility_1y"]["status"] == "valid"


def test_news_relevance_filter_rejects_mismatched_feed():
    from app.providers.misc import filter_relevant
    items = [{"title": "Rayonier (RYN) Tops Q3 Earnings", "summary": None},
             {"title": "Royal Bank of Canada raises dividend", "summary": None},
             {"title": "RBC: RY shares climb after results", "summary": None},
             {"title": "Chevron LNG output", "summary": None}]
    kept, dropped = filter_relevant(items, "RY.TO", "Royal Bank of Canada")
    assert [k["title"][:5] for k in kept] == ["Royal", "RBC: "] and dropped == 2
    kept, _ = filter_relevant([{"title": "Shell buys back shares", "summary": None},
                               {"title": "Chevron record gas output", "summary": None}], "SHEL.L", "Shell plc")
    assert len(kept) == 1
