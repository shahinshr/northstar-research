# Market data sources — recommendation

Companion to `Investment_Research_App_Specification.md` (spec). Researched and **live-tested on 5 Nov 2026**
from this machine. "Tested" = an actual request returned real data today; "Reported" = from docs or
third-party pages only.

## TL;DR

| Stage | Stack | Cost (USD/mo) |
|---|---|---|
| **Build & prototype now** | Yahoo via `yfinance` + SEC EDGAR + Bank of Canada / FRED / Bank of England + ETF-issuer holdings files | **0** |
| **Pilot (recommended)** | FMP **Premium** (US + CA + UK) as licensed primary, free official sources kept, `yfinance` only as a labelled gap-filler | **~59** (annual billing) |
| Optional add-on | EODHD **Historian** for whole-exchange bulk EOD prices (cheap daily scan of 20k listings) | +~17–20 |
| Only if trial proves need | FMP Ultimate (bulk + global) or EODHD All-in-One | 83–149 |

The spec's adapter contract (§11) makes this a drop-in progression: write `YahooAdapter`, `SecEdgarAdapter`,
`RatesAdapter`, `IssuerHoldingsAdapter` first, then add `FmpAdapter` behind the same interface.

## 1. Free sources — what was tested

### Yahoo Finance via `yfinance` 1.2.0 — the best free all-rounder (unofficial)
| Spec input | RY.TO | SHOP.TO | VOD.L | AAPL | Notes |
|---|---|---|---|---|---|
| 10y daily prices | ✅ | ✅ | ✅ | ✅ | ~2,510 bars each |
| Currency / market cap | ✅ CAD | ✅ CAD | ⚠️ `GBp` | ✅ | VOD.L mcap came back ~2.78 **trillion**: GBX scaling bug → spec §14 normalisation is mandatory |
| Quarterly income statement | ✅ | ✅ | ❌ empty | ✅ | UK firms report semi-annually → use annual / H1+H2 TTM construction (spec §14) |
| Annual cash flow | ✅ | ✅ | ✅ | ✅ | ~4–5 years only (spec wants 5 annual / 8 qtr — borderline) |
| EPS/revenue estimates + analyst count | ✅ | ✅ | ✅ | ✅ | |
| **EPS trend: current / 7 / 30 / 60 / 90 days ago** | ✅ | ✅ | ✅ | ✅ | **Big win**: lets the *Estimate-enhanced score* (30/90-day revisions, spec §6) work from day 1 instead of waiting months to accumulate snapshots. Still store own snapshots. |
| Price targets high/low/mean/median | ✅ | ✅ | ✅ | ✅ | Covers CA & UK (FMP's consensus endpoint is US-only per spec §8) |
| News | ❌ | ❌ | ❌ | ❌ | Endpoint failing today ("Yahoo Finance is currently down") |

| Fund input | XIU.TO (CA ETF) | VUSA.L (UK ETF) | VFIAX (US MF) |
|---|---|---|---|
| 10y prices | ✅ | ✅ | ✅ |
| Expense ratio | ✅ 0.18% | ❌ None | ✅ 0.04% |
| Holdings | top 10 only | top 10 only | top 10 only |
| Sector weights | ✅ | ✅ | ✅ |

* **Canadian & UK mutual-fund share classes exist** on Yahoo under Morningstar-style IDs (e.g. RBC Select
  Balanced `0P0000706A.TO`, Fundsmith Equity `0P0000RU81.L` / `0P0000RU7W.L` — acc/inc classes). This is the
  cheapest route to the spec's "expansion" fund coverage; fee/NAV fields need per-class validation.
* Screener: `yf.screen(EquityQuery(...))` returned 250 TSX stocks > USD 300M — usable for universe discovery.
* **Risks:** not an official API; Yahoo ToS is personal-use; HTTP 429 / IP throttling is widely reported
  in 2026; endpoints break without notice (news did today). Fine for a **private** dashboard and dev;
  never the sole production dependency. Cache aggressively, throttle (≈1–2 req/s), and tag every value
  `source=yahoo_unofficial` so data-confidence (spec FR09/FR12) reflects it.
* Throughput: a per-ticker daily scrape of 20k listings is not realistic. Use Yahoo for the ~2,000
  deep-research candidates, and a bulk price source (EODHD/FMP) for the broad scan.

### Official free sources — all ✅ tested, use permanently
| Source | Purpose in spec | Test result |
|---|---|---|
| **Bank of Canada Valet** `/valet/observations/FXUSDCAD,FXGBPCAD,TB.CDN.90D.MID/json` | CAD presentation FX + **CAD cash rate for Sharpe** | 2026-11-04: USDCAD 1.3961, GBPCAD 1.8811, 3m T-bill 2.58% |
| **FRED** `fredgraph.csv?id=DTB3` (no key) | USD cash rate | 2026-11-03: 4.03% |
| **Bank of England** IADB series `IUDSOIA` | GBP cash rate (SONIA) | 2026-11-03: 3.7342% |
| **SEC EDGAR** `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | US fundamentals validation (spec S22) | ✅ Apple facts returned. Needs a descriptive `User-Agent`, ≤10 req/s |

→ The spec says to suppress Sharpe "until a validated rate series is available" — all three currencies are
available free, so Sharpe can ship in v1.

### ETF issuer holdings files — the only free route to *full* holdings (overlap, look-through)
* ✅ **BlackRock Canada (iShares XIU)** CSV: full holdings with ticker, sector, weight, market value, currency,
  "as of" date — exactly what spec §7 overlap/look-through needs.
* ⚠️ iShares US and SSGA (SPY) URLs returned HTML/redirects — need per-issuer URL discovery (fetch product
  page → find the download link). Plan one small scraper per issuer family: iShares (US/CA/UK), Vanguard,
  BMO, SPDR, Invesco. These are public files the issuers publish for download — low legal risk for private use.

### News (Yahoo failing) — free alternatives to wire in
* **Finnhub** free tier (company news, US-focused; ~60 req/min) — needs key; not tested.
* **GDELT** (free, global, unlimited) for news *activity counts* → Attention score's news component (spec §4).
* Issuer RSS / press-release feeds for watchlist names.
* If none reach coverage, follow spec §4: label the score "price-and-volume attention".

### Tested and rejected
* **Stooq** CSV downloads — now behind a JavaScript proof-of-work bot challenge. Not usable headlessly.
* **FMP `demo` key** — no longer works; register a free key (250 calls/day, US only, 500 MB/30 days).
* **Alpha Vantage free** — 25 requests/day; too small beyond spot checks.

## 2. Paid, cheap options (reported — FMP pricing page blocks automated fetch, verify at checkout)

| Plan | Price | Covers | Fit |
|---|---|---|---|
| FMP Basic | free | 250 calls/day, US only | dev only |
| FMP Starter | ~$22/mo (annual) | **US only**, 300 calls/min, 20 GB | ❌ no CA/UK |
| **FMP Premium** | **~$59/mo (annual)** | **US + UK + Canada**, 750 calls/min, 50 GB | ✅ **recommended primary** |
| FMP Ultimate | ~$139–149/mo (third-party sources disagree; spec says $99 annual-equiv) | global, bulk endpoints, 3,000/min, 150 GB | only if bulk is needed |
| EODHD Historian | $19.99/mo or $199/yr | EOD all-world 30+ yrs, splits/divs, 100k calls/day; bulk EOD per exchange ≈100 calls | ✅ cheap whole-universe daily prices |
| EODHD Equity Analyst | $59.99/mo | fundamentals (stocks, ETFs, US MFs) — **no EOD prices** | trap: pair it with Historian or buy All-in-One |
| EODHD All-in-One | $99.99/mo or $999.90/yr | everything above | alternative to FMP |

Note the spec's §9 figure ("FMP Ultimate $99/mo annual-equivalent") conflicts with the third-party figures
found today — confirm on the pricing page before buying. Also confirm whether **bulk** endpoints are
Premium or Ultimate-only; that decides whether Premium alone handles the 20k daily scan
(without bulk: ~20k quotes via batch-quote + statements only on earnings triggers — likely fine at 750/min).

## 3. Coverage map: spec requirement → source

| Spec requirement | Primary | Fallback / validation |
|---|---|---|
| Universe (US/CA/UK stocks, ETFs) | FMP `/company-screener` | Yahoo screener |
| Daily EOD prices, 10 yrs | FMP or EODHD bulk | Yahoo |
| Statements (TTM, 5y/8q) | FMP | Yahoo; SEC EDGAR for US checks |
| Estimates + 30/90d revisions | FMP + own snapshots | **Yahoo `eps_trend` (immediate 7/30/60/90d history)** |
| Price targets CA/UK | Yahoo (FMP is US-only) | — |
| ETF fees / AUM | FMP `/etf/info` | Yahoo `funds_data` |
| Full ETF holdings | Issuer CSV files | FMP `/etf/holdings` |
| US mutual funds | FMP / Yahoo | EODHD |
| CA/UK mutual-fund share classes | Yahoo `0P…` IDs (validate) | fund-company fund-facts / factsheet PDFs (scrape) |
| News activity | FMP news (Premium) | Finnhub, GDELT |
| FX + cash rates (Sharpe) | BoC Valet, FRED, BoE | — |
| **Not available cheaply** | bid–ask spread, NAV premium/discount, bond duration/YTW, moat/fair value | Spec: keep these features disabled/"Conditional" |

## 4. Scraping policy (where no API suffices)
1. ✅ OK for private use: issuer ETF holdings files, fund-company Fund Facts (CA) / KIID-KID & factsheets (UK)
   for OCF/MER, distribution type, hedging; central-bank data.
2. ⚠️ Tolerated, unofficial: Yahoo via `yfinance` — throttle, cache, label provenance.
3. ❌ Do not scrape (spec §21): Morningstar, Koyfin, Stock Rover, Simply Wall St, Trustnet/FT dashboards,
   social media.

## 5. Next step
Implement the spec §22 vertical slice on the $0 stack: **RY.TO** (stock), **XIU.TO** (ETF, full issuer
holdings), **0P0000706A.TO** (Canadian mutual fund) → canonical records → metrics → score explanation →
comparison → manual refresh. Then sign up for FMP Premium and run the §20 100-instrument coverage trial
against Yahoo as the comparison provider.

## Sources
- EODHD pricing: https://eodhd.com/pricing
- FMP pricing (blocked to fetch; third-party summaries): https://multiples.vc/compare/fmp-api-alternatives ,
  https://www.roic.ai/blog/financial-modeling-prep-alternative-roic-ai-vs-fmp ,
  https://site.financialmodelingprep.com/insights/platform/how-to-choose-the-right-financial-modeling-prep-plan-for-your-workflow
- yfinance rate-limit reports: https://github.com/ranaroussi/yfinance/discussions/2431
- Free API comparisons: https://blog.apilayer.com/analyzing-the-top-free-apis-for-stock-data/
- Bank of Canada Valet: https://www.bankofcanada.ca/valet-api-how-to/ · FRED: https://fred.stlouisfed.org/series/DTB3 ·
  SEC EDGAR: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
