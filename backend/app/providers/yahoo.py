"""Yahoo Finance via yfinance — free, unofficial (scrapes Yahoo web endpoints).

Tested 5 Nov 2026: prices, statements, estimates (incl. 7/30/60/90-day EPS trend), price targets for
US/CA/UK. News endpoint was failing. Throttled conservatively; values are labelled with this provider so
data-confidence can reflect the unofficial source.
"""
from __future__ import annotations

import logging
import warnings
from datetime import datetime, timezone

import pandas as pd

from .base import ErrorCode, Provider, ProviderError, ProviderResult, require
from .canonical import clean_fields, num

warnings.filterwarnings("ignore", module="yfinance")
log = logging.getLogger(__name__)

INCOME_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "ebit": ["EBIT"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "eps_diluted": ["Diluted EPS"],
    "diluted_shares": ["Diluted Average Shares"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "pretax_income": ["Pretax Income"],
    "income_tax": ["Tax Provision"],
}
CASHFLOW_MAP = {
    "operating_cash_flow": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "sbc": ["Stock Based Compensation"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "d_and_a": ["Depreciation And Amortization", "Depreciation Amortization Depletion"],
}
BALANCE_MAP = {
    "total_debt": ["Total Debt"],
    "cash": ["Cash And Cash Equivalents"],
    "cash_and_st_investments": ["Cash Cash Equivalents And Short Term Investments"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity"],
    "minority_interest": ["Minority Interest"],
    "preferred_equity": ["Preferred Stock Equity", "Preferred Stock"],
    "total_assets": ["Total Assets"],
    "lease_liabilities": ["Capital Lease Obligations"],
}


def _rows(df: pd.DataFrame | None, mapping: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    """-> {period_end_iso: {field: value}}"""
    out: dict[str, dict[str, float]] = {}
    if df is None or df.empty:
        return out
    for col in df.columns:
        key = pd.Timestamp(col).date().isoformat()
        rec = {}
        for field, names in mapping.items():
            for n in names:
                if n in df.index:
                    v = num(df.at[n, col])
                    if v is not None:
                        rec[field] = v
                        break
        out[key] = rec
    return out


def _ts(v) -> str | None:
    try:
        return datetime.fromtimestamp(int(v), timezone.utc).date().isoformat() if v else None
    except (TypeError, ValueError, OSError):
        return None


def _merge(*parts: dict[str, dict[str, float]], fiscal_period: str) -> list[dict]:
    keys = sorted({k for p in parts for k in p}, reverse=True)
    periods = []
    for k in keys:
        fields: dict[str, float] = {}
        for p in parts:
            fields.update(p.get(k, {}))
        fields = clean_fields(fields)
        if fields:
            periods.append({"period_end": k, "fiscal_period": fiscal_period, "filing_date": None, "fields": fields})
    return periods


class YahooProvider(Provider):
    id = "yahoo"
    name = "Yahoo Finance (unofficial, yfinance)"
    signup_url = "https://finance.yahoo.com"
    min_interval_s = 0.05  # concurrency (4 workers) is the real throttle; 429s trigger chain back-off

    def _ticker(self, symbol: str):
        import yfinance as yf
        self.limiter.wait()
        self.count()
        return yf.Ticker(symbol)

    @staticmethod
    def _wrap(fn):
        try:
            return fn()
        except ProviderError:
            raise
        except Exception as e:  # yfinance raises many exception types
            msg = str(e)
            if "Too Many Requests" in msg or "Rate limit" in msg:
                raise ProviderError(ErrorCode.RATE_LIMITED, msg[:200]) from e
            if "DOWN" in msg.upper():
                raise ProviderError(ErrorCode.UPSTREAM_ERROR, msg[:200]) from e
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, f"{type(e).__name__}: {msg[:200]}") from e

    # ---------------------------------------------------------------- profile
    def fetch_profile(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            info = self._ticker(symbol).info or {}
            require(info.get("quoteType") and (info.get("longName") or info.get("shortName")),
                    ErrorCode.NOT_FOUND, f"no Yahoo profile for {symbol}")
            return ProviderResult(route="yfinance.Ticker.info", payload={
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName"),
                "exchange": info.get("fullExchangeName") or info.get("exchange"),
                "quote_currency": info.get("currency"),
                "financial_currency": info.get("financialCurrency") or info.get("currency"),
                "sector": info.get("sector"), "industry": info.get("industry"),
                "country": info.get("country"),
                "quote_type": (info.get("quoteType") or "").lower(),
                "shares_outstanding": num(info.get("sharesOutstanding")),
                "implied_shares_outstanding": num(info.get("impliedSharesOutstanding")),
                "market_cap": num(info.get("marketCap")),
                "description": info.get("longBusinessSummary"),
                "website": info.get("website"),
                "employees": num(info.get("fullTimeEmployees")),
                "isin": None,
                "most_recent_quarter": _ts(info.get("mostRecentQuarter")),
                "next_earnings": _ts(info.get("earningsTimestampStart") or info.get("earningsTimestamp")),
                "fund_family": info.get("fundFamily"), "category": info.get("category"),
                "vendor_metrics": {  # for cross-checks only (spec §10: not interchangeable)
                    "trailingPE": num(info.get("trailingPE")), "forwardPE": num(info.get("forwardPE")),
                    "enterpriseToEbitda": num(info.get("enterpriseToEbitda")),
                    "beta": num(info.get("beta")), "dividendYield": num(info.get("dividendYield")),
                    "recommendationKey": info.get("recommendationKey"),
                    "recommendationMean": num(info.get("recommendationMean")),
                    "numberOfAnalystOpinions": num(info.get("numberOfAnalystOpinions")),
                    "returnOnEquity": num(info.get("returnOnEquity")), "operatingMargins": num(info.get("operatingMargins")),
                    "revenueGrowth": num(info.get("revenueGrowth")), "freeCashflow": num(info.get("freeCashflow")),
                },
            })
        return self._wrap(go)

    # ---------------------------------------------------------------- prices
    def fetch_prices(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            t = self._ticker(symbol)
            h = t.history(period="10y", interval="1d", auto_adjust=False, actions=True)
            require(h is not None and not h.empty, ErrorCode.EMPTY, f"no Yahoo price history for {symbol}")
            h = h[~h.index.duplicated(keep="last")]
            bars, divs, splits = [], [], []
            for ts, r in h.iterrows():
                d = ts.date().isoformat()
                c = num(r.get("Close"))
                if c is None or c <= 0:
                    continue
                bars.append({"d": d, "o": num(r.get("Open")), "h": num(r.get("High")), "l": num(r.get("Low")),
                             "c": c, "ac": num(r.get("Adj Close")) or c, "v": num(r.get("Volume"))})
                if num(r.get("Dividends")):
                    divs.append({"d": d, "amount": float(r["Dividends"])})
                if num(r.get("Stock Splits")):
                    splits.append({"d": d, "ratio": float(r["Stock Splits"])})
            cur = None
            try:
                cur = t.fast_info.currency
            except Exception:
                pass
            return ProviderResult(route="yfinance.Ticker.history(10y,1d,auto_adjust=False)", payload={
                "quote_currency": cur, "adjustment": "Yahoo Adj Close (split + dividend adjusted)",
                "bars": bars, "dividends": divs, "splits": splits})
        return self._wrap(go)

    # ---------------------------------------------------------------- financials
    def fetch_financials(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            t = self._ticker(symbol)
            annual = _merge(_rows(t.income_stmt, INCOME_MAP), _rows(t.cashflow, CASHFLOW_MAP),
                            _rows(t.balance_sheet, BALANCE_MAP), fiscal_period="FY")
            quarterly = _merge(_rows(t.quarterly_income_stmt, INCOME_MAP), _rows(t.quarterly_cashflow, CASHFLOW_MAP),
                               _rows(t.quarterly_balance_sheet, BALANCE_MAP), fiscal_period="Q")
            require(annual or quarterly, ErrorCode.EMPTY, f"no Yahoo statements for {symbol}")
            try:
                fin_cur = t.info.get("financialCurrency")
            except Exception:
                fin_cur = None
            notes = [] if quarterly else ["No quarterly statements (semi-annual reporter?) — TTM falls back to FY"]
            return ProviderResult(route="yfinance income_stmt/cashflow/balance_sheet (+quarterly)",
                                  status="valid" if annual else "partial", notes=notes,
                                  payload={"financial_currency": fin_cur, "annual": annual, "quarterly": quarterly})
        return self._wrap(go)

    # ---------------------------------------------------------------- estimates
    def fetch_estimates(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            t = self._ticker(symbol)
            ee, re_, tr = t.earnings_estimate, t.revenue_estimate, t.eps_trend
            require(ee is not None and not ee.empty, ErrorCode.EMPTY, f"no Yahoo estimates for {symbol}")
            periods = []
            for p in ee.index:
                row = ee.loc[p]
                rec = {"period": p, "end_date": None,
                       "eps_avg": num(row.get("avg")), "eps_low": num(row.get("low")), "eps_high": num(row.get("high")),
                       "eps_analysts": num(row.get("numberOfAnalysts")), "eps_year_ago": num(row.get("yearAgoEps")),
                       "eps_growth": num(row.get("growth"))}
                if re_ is not None and not re_.empty and p in re_.index:
                    rr = re_.loc[p]
                    rec.update({"revenue_avg": num(rr.get("avg")), "revenue_low": num(rr.get("low")),
                                "revenue_high": num(rr.get("high")), "revenue_analysts": num(rr.get("numberOfAnalysts")),
                                "revenue_growth": num(rr.get("growth"))})
                if tr is not None and not tr.empty and p in tr.index:
                    trr = tr.loc[p]
                    rec["eps_trend"] = {"current": num(trr.get("current")), "d7": num(trr.get("7daysAgo")),
                                        "d30": num(trr.get("30daysAgo")), "d60": num(trr.get("60daysAgo")),
                                        "d90": num(trr.get("90daysAgo"))}
                periods.append(rec)
            return ProviderResult(route="yfinance earnings_estimate/revenue_estimate/eps_trend",
                                  payload={"periods": periods,
                                           "basis": "Vendor consensus; basis (GAAP/adjusted) not disclosed",
                                           "observed_at": datetime.now(timezone.utc).isoformat()})
        return self._wrap(go)

    # ---------------------------------------------------------------- price targets
    def fetch_price_targets(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            t = self._ticker(symbol)
            pt = t.analyst_price_targets or {}
            require(num(pt.get("median")) or num(pt.get("mean")), ErrorCode.EMPTY, f"no Yahoo targets for {symbol}")
            n = None
            try:
                n = num(t.info.get("numberOfAnalystOpinions"))
            except Exception:
                pass
            return ProviderResult(route="yfinance.analyst_price_targets", payload={
                "quote_currency": None, "high": num(pt.get("high")), "low": num(pt.get("low")),
                "mean": num(pt.get("mean")), "median": num(pt.get("median")), "analysts": n, "as_of": None})
        return self._wrap(go)

    # ---------------------------------------------------------------- news
    def fetch_news(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            items = []
            for n in (self._ticker(symbol).news or [])[:30]:
                c = n.get("content", n)
                url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
                       or c.get("link"))
                items.append({"title": c.get("title"), "url": url,
                              "source": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
                              "published_at": c.get("pubDate") or c.get("providerPublishTime"),
                              "summary": c.get("summary")})
            require(items, ErrorCode.EMPTY, "no Yahoo news")
            return ProviderResult(route="yfinance.Ticker.news", payload={"items": items})
        return self._wrap(go)

    # ---------------------------------------------------------------- bulk prices (spec §11: bulk delivery)
    def fetch_prices_bulk(self, symbols: list[str], period: str = "10y") -> dict[str, dict]:
        """One request per chunk of tickers. Returns {symbol: canonical prices payload}; misses are omitted."""
        import yfinance as yf
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 80):
            chunk = symbols[i:i + 80]
            self.limiter.wait()
            self.count()
            try:
                df = yf.download(chunk, period=period, interval="1d", auto_adjust=False, actions=True,
                                 group_by="ticker", threads=True, progress=False)
            except Exception as e:  # noqa: BLE001
                log.warning("bulk download failed for chunk %s: %s", i, e)
                continue
            for sym in chunk:
                try:
                    h = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                h = h.dropna(subset=["Close"])
                h = h[h["Close"] > 0]
                if len(h) < 30:
                    continue
                idx = [ts.date().isoformat() for ts in h.index]
                out[sym] = {
                    "quote_currency": None, "adjustment": "Yahoo Adj Close (split + dividend adjusted)",
                    "bars": {"d": idx, "o": h["Open"].round(4).tolist(), "h": h["High"].round(4).tolist(),
                             "l": h["Low"].round(4).tolist(), "c": h["Close"].round(4).tolist(),
                             "ac": h["Adj Close"].round(4).tolist(), "v": h["Volume"].fillna(0).astype(float).tolist()},
                    "dividends": [{"d": d, "amount": float(v)} for d, v in zip(idx, h.get("Dividends", pd.Series(0, index=h.index))) if v],
                    "splits": [{"d": d, "ratio": float(v)} for d, v in zip(idx, h.get("Stock Splits", pd.Series(0, index=h.index))) if v],
                }
        return out

    # ---------------------------------------------------------------- funds
    def fetch_fund_profile(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            t = self._ticker(symbol)
            info = t.info or {}
            require(info.get("quoteType") in ("ETF", "MUTUALFUND"), ErrorCode.UNSUPPORTED, f"{symbol} is not a fund on Yahoo")
            fd = t.funds_data
            ops = fd.fund_operations
            col = ops.columns[0] if ops is not None and not ops.empty else None
            er_ops = num(ops.at["Annual Report Expense Ratio", col]) if col is not None and "Annual Report Expense Ratio" in ops.index else None
            cat_er = num(ops.at["Annual Report Expense Ratio", "Category Average"]) if col is not None and "Category Average" in ops.columns and "Annual Report Expense Ratio" in ops.index else None
            turnover = num(ops.at["Annual Holdings Turnover", col]) if col is not None and "Annual Holdings Turnover" in ops.index else None
            # Units differ by field: netExpenseRatio is in PERCENT (0.03 = 0.03%); annualReportExpenseRatio and
            # fund_operations are FRACTIONS (0.0194 = 1.94%). Normalise everything to a fraction.
            er, er_src = None, None
            if er_ops:
                er, er_src = er_ops, "funds_data.fund_operations (fraction)"
            elif num(info.get("netExpenseRatio")):
                er, er_src = num(info["netExpenseRatio"]) / 100, "info.netExpenseRatio (percent → fraction)"
            elif num(info.get("annualReportExpenseRatio")):
                er, er_src = num(info["annualReportExpenseRatio"]), "info.annualReportExpenseRatio (fraction)"
            ac = fd.asset_classes or {}
            eq = fd.equity_holdings
            ecol = eq.columns[0] if eq is not None and not eq.empty else None
            def eqv(k):
                return num(eq.at[k, ecol]) if ecol is not None and k in eq.index else None
            bh = fd.bond_holdings
            bcol = bh.columns[0] if bh is not None and not bh.empty else None
            def bv(k):
                return num(bh.at[k, bcol]) if bcol is not None and k in bh.index else None
            sectors = {k: float(v) for k, v in (fd.sector_weightings or {}).items() if num(v)}
            ratings = {k: float(v) for k, v in (fd.bond_ratings or {}).items() if num(v)}
            inc = info.get("fundInceptionDate")
            return ProviderResult(route="yfinance info + funds_data", payload={
                "name": info.get("longName"), "quote_type": info.get("quoteType", "").lower(),
                "family": (fd.fund_overview or {}).get("family") or info.get("fundFamily"),
                "category": info.get("category") or (fd.fund_overview or {}).get("categoryName"),
                "legal_type": info.get("legalType"), "currency": info.get("currency"),
                "expense_ratio": er, "expense_ratio_source": er_src, "category_expense_ratio": cat_er,
                "turnover": turnover, "aum": num(info.get("totalAssets")),
                "aum_note": "Yahoo totalAssets — may be portfolio-level (all share classes)",
                "inception": _ts(inc), "distribution_yield": num(info.get("yield")),
                "nav": num(info.get("navPrice")), "beta_3y": num(info.get("beta3Year")),
                "asset_classes": {k: float(v) for k, v in ac.items() if num(v) is not None},
                "sector_weights": sectors,
                "vendor_valuation": {  # Yahoo reports these as INVERSE ratios (e.g. "Price/Earnings" 0.042 = P/E 23.8)
                    "earnings_yield": eqv("Price/Earnings"), "book_yield": eqv("Price/Book"),
                    "sales_yield": eqv("Price/Sales"), "cashflow_yield": eqv("Price/Cashflow"),
                    "median_market_cap_m": eqv("Median Market Cap"), "earnings_growth_3y": eqv("3 Year Earnings Growth")},
                "bond": {"duration": bv("Duration"), "maturity": bv("Maturity"), "credit_quality": bv("Credit Quality"),
                         "ratings": ratings},
            })
        return self._wrap(go)

    def fetch_holdings(self, symbol: str, region: str | None) -> ProviderResult:
        def go():
            th = self._ticker(symbol).funds_data.top_holdings
            require(th is not None and not th.empty, ErrorCode.EMPTY, "no Yahoo top holdings")
            rows = []
            for sym, r in th.iterrows():
                w = num(r.get("Holding Percent"))
                if w:
                    rows.append({"symbol": str(sym), "name": r.get("Name"), "weight": w, "isin": None, "sector": None,
                                 "asset_class": None, "location": None})
            require(rows, ErrorCode.EMPTY, "no weighted holdings")
            return ProviderResult(route="yfinance funds_data.top_holdings", status="partial",
                                  notes=["Top-10 only — not a complete portfolio (overlap is a lower bound)"],
                                  payload={"complete": False, "as_of": None, "holdings": rows,
                                           "covered_weight": sum(x["weight"] for x in rows)})
        return self._wrap(go)
