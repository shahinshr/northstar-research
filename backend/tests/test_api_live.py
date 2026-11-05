"""End-to-end API tests against the RUNNING server (skipped if it is not up).

Run: .venv/bin/python -m pytest -q tests/test_api_live.py
Checks invariants from the spec: scores 0-100, no zero-filled missing values, unique ranks, provenance
present, deduplicated jobs, validated inputs, export provenance, fund overlap bounds.
"""
import csv
import io
import math

import httpx
import pytest

BASE = "http://127.0.0.1:8000/v1"

try:
    httpx.get(f"{BASE}/health", timeout=2)
    UP = True
except httpx.HTTPError:
    UP = False
pytestmark = pytest.mark.skipif(not UP, reason="API server not running")
c = httpx.Client(base_url=BASE, timeout=60)


@pytest.fixture(scope="module")
def stocks():
    d = c.get("/dashboard", params={"asset": "stock"}).json()
    assert d["run"] is not None, "no published snapshot"
    return d


@pytest.fixture(scope="module")
def funds():
    return c.get("/dashboard", params={"asset": "fund"}).json()


def _finite(x):
    return x is None or (isinstance(x, (int, float)) and math.isfinite(x))


def test_dashboard_invariants(stocks):
    rows = stocks["rows"]
    assert len(rows) > 500, f"expected scaled universe, got {len(rows)}"
    ranks = [r["core"]["rank"] for r in rows if r["core"]["score"] is not None]
    assert len(ranks) == len(set(ranks)), "duplicate core ranks"
    for r in rows:
        s = r["core"]["score"]
        assert s is None or 0 <= s <= 100
        for k, m in r["metrics"].items():
            assert _finite(m["value"]), f"{r['symbol']} {k} not finite"
            if m["status"] != "valid":
                assert m["value"] is None, f"{r['symbol']} {k}: missing value must be null, not {m['value']}"
        if r["core"]["score"] is None:
            assert r["core"].get("reasons"), f"{r['symbol']} unscored without a reason"
    regions = {r["region"] for r in rows}
    assert {"US", "CA", "UK"} <= regions
    assert any(r.get("discovery") for r in rows), "no discovery candidates"


def test_financials_have_no_composite(stocks):
    banks = [r for r in stocks["rows"] if "Bank" in (r.get("industry") or "")]
    assert banks and all(r["core"]["score"] is None for r in banks)


def test_fund_dashboard(funds):
    rows = funds["rows"]
    assert len(rows) >= 50
    cohorts = {r["cohort"] for r in rows}
    assert "US large cap · S&P 500" in cohorts
    bonds = [r for r in rows if r.get("fund_asset_class") == "fixed_income"]
    assert bonds and all(r["core"]["score"] is None for r in bonds), "bond funds must not get a composite"
    sp = [r for r in rows if r["cohort"] == "US large cap · S&P 500" and r["core"]["score"] is not None]
    assert sp and all(r.get("fund_model") == "passive_equity_v1" for r in sp)
    for r in rows:
        er = r["metrics"]["expense_ratio"]
        if er["status"] == "valid":
            assert 0 < er["value"] < 0.03, f"{r['symbol']} expense ratio {er['value']} — unit error?"


@pytest.mark.parametrize("sym", ["AAPL", "SHOP.TO", "VOD.L", "RY.TO"])
def test_stock_analysis_detail(sym):
    iid = next(x["id"] for x in c.get("/instruments", params={"q": sym}).json() if x["symbol"] == sym)
    a = c.get(f"/instruments/{iid}/analysis").json()
    assert a["sources"]["prices"]["provider"]
    assert a["price"]["currency"] in ("USD", "CAD", "GBP")
    if sym == "VOD.L":
        assert a["quote_currency_raw"] == "GBp" and a["price"]["value"] < 10, "GBX not normalised"
    if sym == "SHOP.TO":
        assert a["financial_currency"] == "USD" and a["currency"] == "CAD"
    assert c.get(f"/instruments/{iid}/prices", params={"range": "1y"}).status_code == 200
    assert c.get(f"/instruments/{iid}/financials").status_code == 200


@pytest.mark.parametrize("sym", ["SPY", "VFV.TO", "XBB.TO", "XIU.TO"])
def test_fund_detail(sym):
    iid = next(x["id"] for x in c.get("/instruments", params={"q": sym}).json() if x["symbol"] == sym)
    a = c.get(f"/instruments/{iid}/analysis").json()
    assert a["asset_class"] == "fund" and a["cohort"]
    h = c.get(f"/instruments/{iid}/holdings")
    if sym == "XBB.TO":  # no free source publishes bond-fund holdings: explicit 404, never an empty "valid" list
        assert h.status_code == 404
        assert a["metrics"]["duration"]["status"] == "valid" and a["scores"]["core"]["score"] is None
        return
    assert h.status_code == 200
    if sym in ("SPY", "XIU.TO"):
        assert h.json()["complete"] and len(h.json()["holdings"]) > 50
        assert a["metrics"]["lookthrough_pe"]["status"] in ("valid", "nm")
    t = c.get(f"/instruments/{iid}/tracking").json()
    assert t["available"] == (sym in ("SPY", "VFV.TO"))


def test_compare_and_overlap():
    ids = {x["symbol"]: x["id"] for x in c.get("/instruments", params={"limit": 2000}).json()}
    r = c.post("/comparisons", json={"instrument_ids": [ids["AAPL"], ids["SPY"], ids["VFV.TO"]], "currency": "CAD", "window": "3y"})
    assert r.status_code == 200 and r.json()["dates"]["observations"] > 300
    o = c.post("/overlap", json={"instrument_ids": [ids["SPY"], ids["VOO"], ids["XIU.TO"]]}).json()
    cell = {(x["a"], x["b"]): x for x in o["cells"]}
    assert 0.3 < cell[("SPY", "VOO")]["overlap"] <= 1.0 and not cell[("SPY", "VOO")]["exact"]
    assert cell[("SPY", "XIU.TO")]["overlap"] in (0, 0.0) or cell[("SPY", "XIU.TO")]["overlap"] < 0.05


def test_validation_of_inputs():
    assert c.post("/comparisons", json={"instrument_ids": ["x"], "currency": "CAD", "window": "3y"}).status_code == 422
    assert c.post("/instruments", json={"symbol": "DROP TABLE;"}).status_code == 422
    assert c.put("/settings", json={"timezone": "Mars/Olympus"}).status_code == 422
    assert c.put("/settings", json={"discovery_time": "25:00"}).status_code == 422
    assert c.get("/instruments/does-not-exist/analysis").status_code == 404


def test_export_has_provenance():
    r = c.get("/exports/stock.csv")
    assert r.status_code == 200
    lines = list(csv.reader(io.StringIO(r.text)))
    assert lines[0][0] == "# snapshot" and "as_of" in lines[0]
    assert "currency" in lines[1] and "price_date" in lines[1] and len(lines) > 500


def test_job_deduplication():
    a = c.post("/recalculate").json()
    b = c.post("/recalculate").json()
    assert b["deduplicated"] and a["job_id"] == b["job_id"]


def test_settings_roundtrip():
    before = c.get("/settings").json()
    after = c.put("/settings", json={"discovery_time": "04:15"}).json()
    assert after["discovery_time"] == "04:15"
    c.put("/settings", json={"discovery_time": before["discovery_time"]})


def test_data_health_reports_providers():
    h = c.get("/data-health").json()
    ids = {p["id"]: p for p in h["providers"]}
    assert ids["yahoo"]["configured"] and ids["issuer"]["configured"]
    assert h["universe"]["stocks"] > 500


def test_watchlist_notes_scenarios_alerts():
    iid = next(x["id"] for x in c.get("/instruments", params={"q": "MSFT"}).json() if x["symbol"] == "MSFT")
    assert c.put(f"/watchlist/{iid}").status_code == 200
    assert c.get(f"/instruments/{iid}/analysis").json()["watch"] is True
    assert c.delete(f"/watchlist/{iid}").status_code == 200
    n = c.post(f"/instruments/{iid}/notes", json={"body": "test note"}).json()
    assert any(x["id"] == n["id"] for x in c.get(f"/instruments/{iid}/notes").json())
    assert c.delete(f"/notes/{n['id']}").status_code == 200
    v1 = c.post(f"/instruments/{iid}/scenarios", json={"name": "pytest", "assumptions": {"base": {"wacc": 0.085}}}).json()
    v2 = c.post(f"/instruments/{iid}/scenarios", json={"name": "pytest", "assumptions": {"base": {"wacc": 0.09}}}).json()
    assert v2["version"] == v1["version"] + 1  # versioned, never overwritten (spec §8)
    assert isinstance(c.get("/alerts").json(), list)
