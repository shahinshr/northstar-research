# API keys — what to get, in what order, and how

The app runs today on **free, keyless sources** (Yahoo via yfinance, Yahoo/Google News RSS, SEC EDGAR,
Bank of Canada, FRED, Bank of England, Frankfurter/ECB, GDELT). Each key below *adds a fallback*
behind or ahead of those sources. Nothing breaks if a key is missing — that provider is skipped
(`NOT_CONFIGURED` in Data health).

Put keys in `backend/.env` (copy from `backend/.env.example`), then restart the API.
Keys stay server-side; the browser never sees them.

| Priority | Provider | Cost | What it adds | Sign-up |
|---|---|---|---|---|
| 1 | **Finnhub** | Free | Ticker-linked company news (replaces search-matched Google News as primary), profile backup | https://finnhub.io/register |
| 2 | **Financial Modeling Prep** | Free (US only, 250/day) → **Premium ~USD 59/mo** for US+CA+UK | Licensed primary for statements, prices, estimates, targets, news. Becomes the main source when you scale | https://site.financialmodelingprep.com/register |
| 3 | **EODHD** | Free (20/day) → Historian $19.99/mo | Cheapest whole-exchange daily prices (bulk) for the 20k-listing scan; fundamentals on $59.99+ | https://eodhd.com/register |
| 4 | Alpha Vantage | Free (25/day) | Last-resort US statements/prices | https://www.alphavantage.co/support/#api-key |
| 5 | Twelve Data | Free (800/day) | Extra daily-price backup | https://twelvedata.com/register |
| — | SEC User-Agent | Free, no key | Put **your email** in `SEC_USER_AGENT` (SEC requires a contact) | — |

## Steps

### Finnhub (free, 2 minutes) — recommended now
1. Go to https://finnhub.io/register, sign up with email or Google.
2. The dashboard shows your API key immediately.
3. `backend/.env`: `FINNHUB_API_KEY=your_key`

### Financial Modeling Prep (start free; upgrade when scaling)
1. https://site.financialmodelingprep.com/register → verify email.
2. Dashboard → *API Keys* → copy.
3. `backend/.env`: `FMP_API_KEY=your_key`
4. Free tier only covers **US** symbols. Before buying Premium, ask FMP support in writing to confirm:
   (a) TSX + LSE coverage on Premium, (b) whether bulk endpoints need Ultimate, and (c) that a private
   personal dashboard is permitted under their display terms (spec §9).

### EODHD (only when we scale past ~2,000 symbols)
1. https://eodhd.com/register → API token on the dashboard.
2. `backend/.env`: `EODHD_API_KEY=your_token`
3. Note the trap: the $59.99 *Fundamentals* plan has **no** EOD prices — pair with Historian or buy All-in-One.

### Alpha Vantage / Twelve Data (optional)
Sign up at the links above; set `ALPHAVANTAGE_API_KEY` / `TWELVEDATA_API_KEY`.

## Changing fallback order
Each dataset has a chain in `.env`, e.g. `CHAIN_FINANCIALS=yahoo,fmp,eodhd,alphavantage,sec_edgar`.
Once you have FMP Premium, move it first: `CHAIN_FINANCIALS=fmp,yahoo,eodhd,sec_edgar`.
