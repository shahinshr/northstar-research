# Northstar Research — investment discovery dashboard (v0.2)

Implements `Investment_Research_App_Specification.md` on free data with multi-provider fallback:
~1,150 stocks across North America, Europe and Asia-Pacific/emerging markets:
- **Americas & UK:** S&P 500, S&P/TSX, FTSE 100.
- **Asia-Pacific:** ASX 200, Hang Seng, SSE 50, NIFTY 50.
- **Europe:** DAX, CAC 40, SMI, Euro Stoxx 50.

It also covers ~90 ETFs and mutual funds in comparable cohorts. The data refreshes automatically, with intraday quotes
and breaking news while markets are open and full analysis daily. A validation suite tests the data and the ranking
against independent sources.

## Run it

```bash
# backend (Python 3.12)
cd backend
cp .env.example .env            # add keys (optional) — see docs/api-keys.md
.venv/bin/uvicorn app.main:app --port 8000
#   first start: syncs the universe and runs discovery in the background (~40-60 min on free sources)

# frontend (Node 20+)
cd frontend && npm run dev       # http://localhost:3000
```
Fresh machine: `python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt`
and `cd frontend && npm install`.

Tests (43: unit + live API):
- `cd backend && .venv/bin/python -m pytest -q`: unit tests, plus live API tests when the server is up.
- **Validation:** the Validation page, or `POST /v1/validation-runs`.

## What's in it

| Area | Spec | Notes |
|---|---|---|
| Universe sync + bulk prices + parallel enrichment | §4, §11, §15 | Wikipedia → issuer file → last-list fallback; earnings-triggered statement refresh |
| Stock model core_v1 / estimate-enhanced / attention | §4–6 | sector/industry peer percentiles; "Why on list" discovery reasons |
| Funds: cost, tracking vs TR index, diversification, look-through P/E, overlap | §7 | passive_equity_v1, basic_fund_screen_v1; bond and active funds get dashboards only |
| DCF scenarios with sensitivity | §8 | client-side; not yet persisted |
| Provenance, fallback trails, data health, provider call counts | §10–12, FR09/12 | |
| Alerts, CSV export, settings (universe, schedule, time zone) | §3, FR10–11 | |
| Validation: cross-provider checks, walk-forward backtest, analyst agreement, fund cost test | §14, §20 | biases stated in the report |
| Auth / multi-user, AI assistant, licensed fund data | §18–19 | not yet — single private user |

Docs:
- `docs/strategy.md`: why the models look the way they do.
- `docs/data-sources.md`: provider research.
- `docs/api-keys.md`: sign-up steps.
