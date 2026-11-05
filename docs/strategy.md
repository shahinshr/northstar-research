# Selection strategy & data — what the app does and why

Researched 5 Nov 2026. This explains the evidence behind each model, the data used for each asset class,
and the limits. It is a research method, not investment advice.

## 1. Stocks — multi-factor, peer-relative (core_v1)

| Component | Weight | Inputs | Evidence |
|---|---|---|---|
| Value | 30% | FCF yield, earnings yield, EBITDA/EV | Long-run value premium (Fama–French); CFA multiples/DCF practice |
| Quality | 25% | ROIC, operating margin, cash conversion | MSCI quality methodology (profitability, stability, leverage) |
| Growth | 20% | Revenue and EPS growth | Damodaran: growth only adds value with returns on capital above cost |
| Resilience | 15% | Net debt/EBITDA, interest coverage | Leverage is a core quality screen |
| Momentum | 10% | 12-month return excl. last month | Momentum premium; negatively correlated with value, so it diversifies |

Why combine: single factors go through long droughts (e.g. quality lagged the MSCI World in 2025, 16.9% vs
21.6%). Value and momentum are negatively correlated, so a blend smooths the path. Studies also find factor
premiums persist but are **smaller than first reported** once costs are included
([Advisor Perspectives 2026](https://www.advisorperspectives.com/commentaries/2026/06/04/factor-investing-endures-tough-2025-quality-stocks),
[CXO Advisory](https://www.cxoadvisory.com/?p=32628)). The app therefore treats the score as a *research
shortlist*, not a forecast, and tests it out of sample on the Validation page.

How the ranking works:
- **Peer-relative:** percentiles within the industry if it has ≥ 30 members, else the sector, else the region,
  else the whole universe (shown per input).
- **Data cleaning:** extreme values capped at the 2nd/98th percentiles; tied values share a rank.
- **Missing data:** at least 80% input coverage is required, or no score is given ("Insufficient data").
- **Separate models pending:** banks, insurers, REITs and loss-makers are not given a composite.

**Discovery** is a union of lists so one theme can't dominate: composite leaders, best value, best quality,
best growth, rising estimates and unusual attention (top decile of each).

## 2. ETFs & index funds — cost first, then tracking

The most robust fund finding is that **cost predicts outcomes**. In Morningstar's study the cheapest
quintile succeeded 62% of the time vs 20% for the priciest in US equity, and 59% vs 17% in taxable bonds
([Morningstar](https://www.morningstar.com/funds/fund-fees-predict-future-success-or-failure)). Active
management mostly fails net of fees: SPIVA Canada 2025 found 93.1% of Canadian-focused active equity funds
trailed their benchmark ([S&P SPIVA](https://www.spglobal.com/spdji/en/spiva/article/spiva-canada-year-end-2025/)).

The models (spec §7):
- **Passive fund score v1:** cost 40%, tracking 30% (tracking difference 60%, tracking error 40%),
  diversification 20% (top-10 weight), liquidity 10% (assets). Used only where a total-return index exists
  (S&P 500 → ^SP500TR, Nasdaq-100 → ^XNDX).
- **Basic fund screen v1:** cost 50%, diversification 20%, scale 15%, 3-year drawdown 15%. Used for other
  passive cohorts and clearly named.
- **No composite** for bond funds or active funds; they get a category dashboard instead. A longer-duration
  bond fund must not win just because its yield is higher.
- **Cohorts:** funds are only compared within a cohort with the same benchmark or strategy, e.g. all S&P 500
  trackers across US, Canadian and UK listings.

Tracking difference includes fees **and foreign withholding tax**. A Canadian or UK-listed S&P 500 ETF lags
the index by its fee plus roughly 0.2%/yr of tax on US dividends. That is a real cost, and the Validation page
measures it.

## 3. Data sources by asset class (all free, each with fallbacks)

| Need | Primary | Fallbacks | Notes |
|---|---|---|---|
| Universe | Wikipedia constituents | iShares XIC holdings → last stored list | S&P 500, S&P/TSX Composite, FTSE 100 (+FTSE 250) |
| Prices (bulk) | Yahoo `download` | FMP → EODHD → Twelve Data → Alpha Vantage | about 820 stocks in ~1 minute |
| Statements | Yahoo | FMP → EODHD → Alpha Vantage → SEC EDGAR | SEC used to validate US figures |
| Estimates & revisions | Yahoo (7/30/60/90-day trend) | FMP → EODHD | revisions available from day one |
| News | Finnhub (US) | Yahoo RSS → Google News → GDELT | relevance-filtered |
| Fund costs & profile | Yahoo funds_data | FMP → EODHD | unit-normalised (percent vs fraction) |
| Full holdings | Issuer files (SSGA SPY, iShares XIU/XIC) | FMP → EODHD → Yahoo top-10 | top-10 overlap shown as a lower bound |
| Index total return | ^SP500TR, ^XNDX | — | for tracking difference |
| FX, cash rates | Bank of Canada, FRED, Bank of England | ECB/Frankfurter, Yahoo | used for currency-matched Sharpe ratios |

Known gaps (shown as N/A, never guessed):
- **OCF:** Vanguard UK ETFs.
- **Fees:** Fundsmith's OCF.
- **Fund categories:** missing for non-US funds, so the app supplies its own cohorts.
- **Trading data:** bid–ask spreads and NAV premium/discount.
- **Holdings:** full holdings for funds without an issuer file.

The paid upgrade that closes most gaps is FMP Premium; Morningstar is the one to evaluate for complete
Canadian/UK mutual-fund share classes.

## 4. How to use the output
1. Shortlist with presets and discovery lists.
2. Read *why* a stock scores: drill into components, inputs, peer group and data confidence.
3. Check the adverse evidence: drawdown, leverage, dilution, IFRS lease caveats.
4. Value it yourself in the DCF workspace.
5. For funds, pick the cheapest well-tracking fund in the cohort that matches the exposure you want, and
   check overlap with what you already hold.

## 5. Validation results (5 Nov 2026 run — see the Validation page for the live report)

| Test | Result | Reading |
|---|---|---|
| Prices vs Finnhub (40 US stocks, ±0.5%) | 100% pass, median diff 0.00% | Price data is reliable |
| Statements vs SEC 10-K (±2%) | 93% pass | Exceptions are definitional: bank "revenue"; income to common holders after preferred dividends (HBAN, KKR) |
| Metrics vs Finnhub | Market cap 92%, P/E 87%, operating margin 68% | Different TTM definitions; one-off charges (e.g. BIIB) flagged for review |
| **Core score backtest**: 398 S&P 500 stocks, Dec 2017 → Jun 2026, 35 quarters, SEC data lagged 90 days | Top quintile 20.0%/yr (19.8% net of costs) vs equal-weight 15.3%, SPY 14.4%, bottom quintile 14.8% | Positive but **not statistically significant**: rank IC 0.018, t = 1.1, quintiles not monotonic. Survivorship-biased |
| Component signal (mean IC) | Resilience 0.018, growth 0.013, value 0.008, quality 0.007, momentum ≈ 0 | Momentum added nothing over this period; keep weights until more evidence (don't overfit) |
| Agreement with analysts | ρ ≈ 0.04 (target upside), −0.03 (rating) | The score is independent of analyst opinion: use it as a second opinion, not a confirmation |
| **Funds**: fee vs tracking difference (S&P 500 cohort) | ρ = −0.72 | Strong: cheaper funds tracked better. US-listed funds lag by fee only (−0.03% unexplained); CA/UK listings lag ~0.30% more (withholding tax); CAD-hedged XSP lags 2.3% (hedging cost) |

**Bottom line.** The data pipeline is accurate. The fund model rests on strong, verified evidence. The stock
score is a reasonable research filter with weak historical evidence, consistent with the literature that
factor premiums are real but small and noisy. Treat a high score as a reason to research, not to buy.
