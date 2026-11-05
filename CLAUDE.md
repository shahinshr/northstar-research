# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A private investment research dashboard implementing `Investment_Research_App_Specification.md` (the spec; section
numbers like "spec §7" in code comments refer to it). The default `global` universe is ~1,150 stocks: US/CA/UK plus
ASX 200, Hang Seng, SSE 50, NIFTY 50, DAX, CAC 40, SMI and Euro Stoxx 50. There is also a ~90-fund curated
catalogue that includes China, India, Japan, Europe and emerging-market ETFs. It runs on free data sources with multi-provider fallback. `docs/strategy.md` explains why the models look the
way they do and records validation results. `docs/data-sources.md` and `docs/api-keys.md` cover the providers.

## Commands

```bash
# Backend (Python 3.12 venv already in backend/.venv)
cd backend
.venv/bin/uvicorn app.main:app --port 8000            # no --reload: restart after backend code changes
.venv/bin/python -m pytest -q                          # all tests
.venv/bin/python -m pytest -q tests/test_funds.py::test_overlap_is_sum_of_min_weights_and_flags_lower_bound
.venv/bin/python -m pytest -q tests/test_analytics.py tests/test_funds.py   # unit only (no server needed)

# Frontend (Next.js 15, Node 20+)
cd frontend
npm run dev                                            # http://localhost:3000, proxies /api/* -> 127.0.0.1:8000
npx tsc --noEmit && npx eslint src --quiet             # type-check + lint (use these while dev server runs)
```

- **Live tests:** `tests/test_api_live.py` hits the running server on :8000 and auto-skips if it is down. It needs a
  completed scaled snapshot (it asserts >500 stock rows).
- **Don't build during dev:** do not run `next build` while `npm run dev` is running. It overwrites `.next` and breaks the
  dev server (fix: stop dev, `rm -rf .next`, restart).
- **Recalculate after analytics changes:** `POST /v1/recalculate` re-runs all formulas on stored data with no provider
  traffic (~30 s), so it is the way to see the effect of a change. A full refresh is `POST /v1/discovery-runs?mode=force`
  (40–60 min; free Yahoo throttles).
- **Deep links for screenshots:** `/instrument/<id>?tab=valuation`, `/?asset=fund`, `?theme=dark`. Headless Chrome works:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --virtual-time-budget=30000 --screenshot=...`
- **Dev-server caching:** after editing `tailwind.config.ts`, restart `npm run dev` and `rm -rf .next`; the dev server
  caches the config and fails with "class does not exist".
- **Data and config locations:** SQLite DB at `backend/data/app.db`. API keys go in `backend/.env` (template
  `.env.example`). Keep `# comments` on their own lines there: python-dotenv reads `KEY=  # comment` as the value.

## Architecture

### Data flow
1. A job (`services/jobs.py`, single-thread executor, deduplicated by scope key, persisted in `jobs`) runs
   `sync_universe` → `refresh_macro` → `refresh_many` → `compute_run` → `prune`.
2. `refresh_many` (`services/ingest.py`) bulk-downloads prices via Yahoo first, then enriches the remaining datasets in a
   4-worker pool. Each worker has its own DB session.
3. Every fetch is appended as a versioned `DatasetSnapshot` holding provider, route, content hash and the full fallback
   `attempts` trail.
4. `compute_run` (`services/analysis.py`) reads the latest valid snapshot per dataset and computes metrics, peer
   percentiles and scores. It then writes an immutable `AnalysisRun` with one `AnalysisResult` per instrument: full
   `payload` plus a slim `row` used by the dashboard.
5. The API always serves the latest *completed* run. A quality gate refuses to publish if coverage collapses.

### Freshness (auto-refresh without hammering providers)
- **Daily run:** fundamentals refresh on the daily discovery job (04:00 Toronto, `DailyScheduler`).
- **Intraday job:** `IntradayScheduler` submits a `refresh_quotes` job every `intraday_minutes` (setting, default 20)
  while any market is open. It bulk-fetches 5-day bars into a small `quote` dataset, never rewriting the 10-year
  `prices`. It also refreshes news for the watchlist plus the top 40, then runs `compute_run(kind="intraday")`.
- **Price merge:** always read prices through `ingest.load_prices`, which merges the `quote` bars over the history and
  chains adjusted closes from the last stored bar. Today's unfinished bar is exposed as a *provisional* `live` quote and
  is never used in scores.
- **On-view refresh:** `services/live.py` refreshes the quote and news of the instrument being viewed, on its own small
  pool (dedup + 5-minute gap). `GET /v1/instruments/{id}/live` always answers from the DB immediately.
- **Run kinds:** each `AnalysisRun.manifest.kind` is `daily` or `intraday`. Alerts and "changes" compare against
  `latest_daily_run`, so intraday price moves don't spam alerts. `prune_runs` keeps a few intraday runs and 30 days of
  daily runs.
- **Frontend polling:** `Nav`'s `LiveStatus` polls `/v1/status` and invalidates queries when a new run id appears.
  Pages also poll (dashboard 60 s, live quote/news 30 s).
- **News tagging:** `services/news.py` is a transparent lexicon (severity, tags, polarity). News text is untrusted and
  only classified. `/v1/pulse` gathers critical/high stories across the watchlist and candidates. Social feeds are
  link-outs only: X API is paid, and StockTwits is Cloudflare-blocked.

### Providers (`app/providers/`)
- **Adapter contract:** each adapter subclasses `Provider` and implements `fetch_<dataset>(symbol, region)`. It returns
  canonical payloads (shapes documented in `canonical.py`) or raises a typed `ProviderError` (`ErrorCode`).
- **Missing data:** empty or missing values must raise or be `None`, never `0`.
- **Fallback chains:** `registry.run_chain` walks the per-dataset chain from `config.py` / `CHAIN_*` env vars. Unconfigured
  providers are skipped. Partial results are kept only as a fallback while later providers are tried for a fully valid one.
- **Datasets:** `profile, prices, financials, estimates, price_targets, news, fund_profile, holdings`, plus macro `fx`,
  `cash_rates` and `constituents` stored under subject `"macro"`.
- **Ticker format:** canonical symbols are Yahoo-style (`RY.TO`, `VOD.L`, `BRK-B`, `0700.HK`, `600519.SS`). Adapters
  map to their own formats (e.g. EODHD `VOD.LSE`).
- **Regions:** `canonical.region_of` maps the suffix to a region (US, CA, UK, EU, CH, AU, HK, CN, IN…). Charts colour
  by the three `region_group`s, the colour-safe maximum for scatter plots.
- **Exchange hours:** `analytics/market.EXCHANGE_CLOSE` / `market_open` hold per-region hours. A new region needs an
  entry there, a `BENCHMARKS` proxy and FX coverage in `macro.FX_CCYS`.

### Analytics (`app/analytics/`): pure functions, unit-tested
- **Metric shape:** every metric is `M(value, basis, status, reason)` with status `valid | nm | na`. The UI renders N/M or
  N/A with the reason on hover. Preserve this when adding metrics.
- **Scoring models:** defined as data in `scoring.py`: `CORE_V1`, `ESTIMATE_V1`, attention models, `PASSIVE_EQUITY_V1`,
  `BASIC_FUND_SCREEN_V1`.
- **Percentiles:** `peer_percentiles` picks the narrowest peer group with ≥ N valid members (stocks: industry → sector →
  region → universe, N=30; funds: cohort → family → universe, N=3).
- **Coverage rules (spec §6):** `score_model` requires ≥80% total input coverage and ≥50% per component, else no score.
- **Fund models:** passive funds with an index total-return series (`^SP500TR`, `^XNDX`) use the passive model; other
  passive funds use the basic screen. Fixed-income and active funds deliberately get no composite.
- **No composite for some stocks:** financial-sector industries (`FINANCIAL_EXCLUSIONS`) and loss-makers are excluded from
  `core_v1`. Their operating metrics are forced to `na`.

### Universe (`services/universe.py`)
- **Stocks:** Wikipedia constituent tables, with the iShares XIC file and then the last stored list as fallbacks.
- **Funds:** `FUND_CATALOGUE` hand-assigns cohort, asset class, style, index and hedged flag; this is the source of fund peer
  groups. Full holdings come from issuer files (`ssga:` / `blackrock_ca:` refs); other funds only have Yahoo top-10, so
  overlap is a lower bound.
- **Benchmarks:** SPY / XIU.TO / ISF.L are both catalogue funds and regional benchmarks (`tags.benchmark_for`).

### Frontend (`frontend/src/`)
- **Structure:** all client components using TanStack Query against `lib/api.ts`. Metric labels, formats and help text live
  in `lib/metrics.ts`; add an entry there for any new metric.
- **Jobs in the UI:** `components/jobs.tsx` tracks jobs and persists the job id in localStorage.
- **Funds vs stocks:** `/instrument/[id]` renders `FundDetail` when `asset_class === "fund"`. Shared tabs live in
  `components/InstrumentParts.tsx`; Next page files may only export the default component.
- **DCF:** `components/Dcf.tsx` runs client-side with a sales-to-capital reinvestment model. Defaults are normalised to
  fiscal-year history (`anchors.fy_margins`), and scenarios save via `/v1/instruments/{id}/scenarios`.
- **Colours:** Tailwind colours are CSS variables in `globals.css`, with the categorical palette `--s1..--s8`. Opacity
  modifiers like `bg-accent/10` don't work on them; use `color-mix(...)`.

### Validation (`app/validation/run.py`)
The Validation page and `POST /v1/validation-runs` run four checks:
- **Data cross-checks:** prices and metrics vs Finnhub, statements vs SEC 10-K.
- **Walk-forward backtest:** `core_v1` on current S&P 500 members using SEC XBRL *frames* lagged 90 days, cached in
  `backend/data/validation/`.
- **Analyst agreement.**
- **Fund cost test:** expense ratio vs tracking difference.

Reports are stored as snapshot `subject="validation"`. Use the local `spearman()` helper: scipy is not installed, so pandas
`corr(method="spearman")` fails.

## Pitfalls already hit (don't reintroduce)
- **Expense-ratio units:** Yahoo `netExpenseRatio` is in **percent**; `annualReportExpenseRatio` and `fund_operations` are
  **fractions**. Normalised in `yahoo.fetch_fund_profile`.
- **Yahoo fund P/E:** Yahoo fund "Price/Earnings" is actually an earnings yield (inverse).
- **GBp quotes:** prices quoted in GBp/GBX are normalised by `normalise_quote_currency`. Statements are converted from
  reporting to trading currency before any ratio (e.g. SHOP.TO reports in USD; VOD.L trades in GBp and reports in EUR).
- **Partial sessions:** today's bar is dropped until the exchange closes (`market.bars_frame`).
- **Stale session cache:** `SessionLocal` uses `expire_on_commit=False`. `compute_run` calls `s.expire_all()` because
  workers update instruments in other sessions; without it, sectors were stale and peer groups collapsed to region.
- **Look-through valuation:** never average P/Es. Use Σ weight × earnings yield. Funds of funds (VFV holds VOO; XEQT holds
  ETFs) are expanded via `funds.expand_holdings`.
- **zsh:** unquoted `$var` is not word-split, so `set -- $ids` doesn't work in shell scripts here.
