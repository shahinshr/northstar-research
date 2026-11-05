"""Fund analytics, scoring and discovery tests (spec §7, §4, §20)."""
import math

import numpy as np
import pandas as pd

from app.analytics import funds, scoring
from app.analytics.market import bars_frame, risk_metrics
from app.providers.funds import yahoo_symbol
from app.services.analysis import tag_discovery


def _series(daily: float, n: int = 900, start="2023-01-02", noise=0.0, seed=1) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    r = daily + rng.normal(0, noise, n) if noise else np.full(n, daily)
    return pd.Series(100 * np.cumprod(1 + r), index=idx)


def test_tracking_difference_equals_fee_drag():
    idx = _series(0.0004)
    fee = 0.0009  # 0.09% a year deducted daily
    fund = idx * np.exp(-fee * np.arange(len(idx)) / 252)
    m = funds.tracking_metrics(fund, idx, "TEST")
    assert math.isclose(m["tracking_difference"]["value"], -fee, abs_tol=2e-4)
    assert m["tracking_error"]["value"] < 0.001


def test_tracking_unavailable_without_index_or_history():
    assert funds.tracking_metrics(_series(0.0003), None, "")["tracking_difference"]["status"] == "na"
    short = _series(0.0003, n=100)
    assert funds.tracking_metrics(short, short, "X")["tracking_difference"]["status"] == "na"


def test_overlap_is_sum_of_min_weights_and_flags_lower_bound():
    a = {"complete": True, "holdings": [{"symbol": "AAPL", "weight": 0.07}, {"symbol": "MSFT", "weight": 0.06}, {"symbol": "X", "weight": 0.87}]}
    b = {"complete": False, "holdings": [{"symbol": "AAPL", "weight": 0.10}, {"symbol": "MSFT", "weight": 0.02}]}
    o = funds.overlap(a, b)
    assert math.isclose(o["overlap"], 0.07 + 0.02) and o["common"] == 2 and o["exact"] is False
    assert funds.overlap(a, None)["overlap"] is None


def test_lookthrough_uses_weighted_earnings_yield_not_average_pe():
    hold = {"complete": True, "holdings": [{"symbol": "A", "weight": 0.5}, {"symbol": "B", "weight": 0.45}, {"symbol": "C", "weight": 0.05}]}
    ey = {"A": (0.05, True), "B": (0.02, True)}  # P/E 20 and 50
    m = funds.look_through(hold, ey, {"earnings_yield": 0.04})
    exp = (0.5 * 0.05 + 0.45 * 0.02) / 0.95
    assert math.isclose(m["lookthrough_earnings_yield"]["value"], exp)
    assert math.isclose(m["lookthrough_pe"]["value"], 1 / exp)  # NOT mean(20, 50) = 35
    assert math.isclose(m["vendor_pe"]["value"], 25.0)  # vendor inverse convention handled
    partial = funds.look_through({"complete": False, "holdings": hold["holdings"]}, ey, None)
    assert partial["lookthrough_pe"]["status"] == "nm"  # top-10 data never yields a headline P/E


def test_bond_metrics_do_not_infer_duration():
    m = funds.bond_metrics({"distribution_yield": 0.04, "bond": {"duration": None, "ratings": {"aaa": 0.5, "aa": 0.2, "bb": 0.1}}})
    assert m["duration"]["status"] == "na" and m["yield_per_duration"]["status"] == "na"
    assert math.isclose(m["high_grade_share"]["value"], 0.7)


def test_passive_model_prefers_cheaper_fund_in_cohort():
    base = {"tracking_difference": -0.001, "tracking_error": 0.001, "top10_weight": 0.35, "aum_usd": 1e10}
    subj = {}
    for sym, er in (("CHEAP", 0.0003), ("MID", 0.0009), ("DEAR", 0.0045)):
        mets = {k: {"value": v, "status": "valid"} for k, v in {**base, "expense_ratio": er}.items()}
        subj[sym] = {"metrics": mets, "cohort": "S&P 500", "family": "equity · passive"}
    pc = scoring.peer_percentiles(subj, scoring.all_metric_keys(scoring.PASSIVE_EQUITY_V1), 3, levels=scoring.FUND_PEER_LEVELS)
    scores = {s: scoring.score_model(scoring.PASSIVE_EQUITY_V1, subj[s]["metrics"], pc[s])["score"] for s in subj}
    assert scores["CHEAP"] > scores["MID"] > scores["DEAR"]


def test_drawdown_requires_full_window():
    out = risk_metrics(_series(0.0003, n=400), None, None)
    assert out["max_drawdown_1y"]["status"] == "valid"
    assert out["max_drawdown_3y"]["status"] == "na"  # no padded pre-inception history


def test_columnar_price_payload_is_readable():
    payload = {"bars": {"d": ["2026-01-02", "2026-01-05"], "o": [1, 1], "h": [1, 1], "l": [1, 1], "c": [10.0, 11.0],
                        "ac": [10.0, 11.0], "v": [100.0, 120.0]}}
    df, _ = bars_frame(payload, "US", 0.01)
    assert list(df["c"]) == [0.10, 0.11]


def test_issuer_ticker_mapping():
    assert yahoo_symbol("RY", "Toronto Stock Exchange") == "RY.TO"
    assert yahoo_symbol("BRK.B", None) == "BRK-B"
    assert yahoo_symbol("SHEL", "London Stock Exchange") == "SHEL.L"


def test_discovery_is_a_union_of_lists():
    subj = {}
    for i in range(20):
        comps = {"value": {"score": i * 5}, "quality": {"score": 100 - i * 5}, "growth": {"score": 50}}
        subj[str(i)] = {"status": "ok", "scores": {"core": {"score": 50, "components": comps}, "attention": {"score": 10}},
                        "metrics": {"eps_revision": {"value": -0.01, "status": "valid"}}}
    tag_discovery(subj)
    assert "Attractive valuation" in subj["19"]["discovery"] and "High quality" in subj["0"]["discovery"]
    assert not any("Rising estimates" in v.get("discovery", []) for v in subj.values())  # negative revisions never qualify


def test_spearman_helper_matches_definition():
    from app.validation.run import spearman
    assert math.isclose(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
    assert math.isclose(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
    assert math.isclose(spearman([1, 2, None, 4, 5], [2, 1, 9, 4, 5]), 0.8)


def test_fund_of_funds_look_through_and_cycles():
    voo = {"complete": False, "holdings": [{"symbol": "AAPL", "weight": 0.07}, {"symbol": "MSFT", "weight": 0.06}]}
    vfv = {"complete": True, "holdings": [{"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "weight": 1.0}]}
    spy = {"complete": True, "holdings": [{"symbol": "AAPL", "weight": 0.075}, {"symbol": "MSFT", "weight": 0.065}]}
    look = funds.expand_holdings(vfv, {"VOO": voo, "VFV.TO": vfv})
    assert {h["symbol"] for h in look["holdings"]} == {"AAPL", "MSFT"} and look["complete"] is False
    assert math.isclose(funds.overlap(look, spy)["overlap"], 0.13)
    cyc = {"complete": True, "holdings": [{"symbol": "A", "weight": 1.0}]}
    assert funds.expand_holdings(cyc, {"A": cyc})["holdings"]  # terminates on a cycle
    d = funds.diversification_metrics({}, vfv, funds.fund_share(vfv, {"VOO"}))
    assert d["top10_weight"]["status"] == "na"


def test_roic_not_meaningful_for_net_cash_balance_sheet():
    from app.analytics.fundamentals import compute_stock_fundamentals
    fin = {"financial_currency": "USD", "quarterly": [], "annual": [
        {"period_end": "2025-12-31", "fiscal_period": "FY", "filing_date": None,
         "fields": {"revenue": 100, "ebit": 10, "operating_income": 10, "net_income": 8, "total_debt": 1,
                    "total_equity": 40, "cash": 38, "total_assets": 100, "income_tax": 2, "pretax_income": 10}}]}
    m = compute_stock_fundamentals(fin, price=10, market_cap=1000, fx_fin_to_px=1.0)
    assert m["roic"]["status"] == "nm"


def test_quote_bars_merge_keeps_history_and_chains_adjusted_close():
    from app.services.ingest import merge_quote_bars
    hist = {"d": ["2026-11-03", "2026-11-04"], "o": [1, 1], "h": [1, 1], "l": [1, 1], "c": [100.0, 110.0], "ac": [50.0, 55.0], "v": [1, 1]}
    quote = [{"d": "2026-11-03", "c": 999.0, "ac": 999.0}, {"d": "2026-11-04", "c": 110.0, "ac": 108.0},
             {"d": "2026-11-05", "c": 121.0, "ac": 119.0}]
    out = merge_quote_bars(hist, quote)
    assert [b["d"] for b in out] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert out[0]["c"] == 100.0  # history wins for past dates
    assert math.isclose(out[2]["ac"], 55.0 * 1.1)  # +10% raw move chained onto the stored adjusted series
