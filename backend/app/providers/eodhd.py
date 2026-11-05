"""EODHD (https://eodhd.com/api). Tickers use CODE.EXCHANGE: AAPL.US, RY.TO, VOD.LSE.

Cost model (spec §15): EOD = 1 credit, fundamentals = 10, news = 5. Fundamentals need the
Fundamentals or All-in-One plan; the Historian plan has prices only.
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import ErrorCode, Provider, ProviderError, ProviderResult, require
from .canonical import clean_fields, num, split_symbol

BASE = "https://eodhd.com/api"
SUFFIX = {"US": "US", "TO": "TO", "V": "V", "L": "LSE", "NE": "NEO"}


def eod_symbol(symbol: str) -> str:
    base, suf = split_symbol(symbol)
    return f"{base.replace('-', '.') if suf == 'US' else base}.{SUFFIX.get(suf, suf)}"


class EODHDProvider(Provider):
    id = "eodhd"
    name = "EODHD"
    requires_key = True
    signup_url = "https://eodhd.com/register"
    min_interval_s = 0.1

    def _get(self, route, **params):
        params.update({"api_token": self.api_key, "fmt": "json"})
        d = self.get_json(f"{BASE}{route}", params=params)
        if isinstance(d, str) or (isinstance(d, dict) and d.get("error")):
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, str(d)[:200])
        return d

    def _fund(self, symbol):
        d = self._get(f"/fundamentals/{eod_symbol(symbol)}")
        require(isinstance(d, dict) and d.get("General"), ErrorCode.NOT_FOUND, f"EODHD fundamentals empty for {symbol}")
        return d

    def fetch_profile(self, symbol, region):
        d = self._fund(symbol)
        g, h, ss = d.get("General", {}), d.get("Highlights", {}), d.get("SharesStats", {})
        return ProviderResult(route="/fundamentals (General)", payload={
            "symbol": symbol, "name": g.get("Name"), "exchange": g.get("Exchange"),
            "quote_currency": g.get("CurrencyCode"), "financial_currency": None,
            "sector": g.get("Sector"), "industry": g.get("Industry"), "country": g.get("CountryName"),
            "quote_type": (g.get("Type") or "").lower().replace("common stock", "equity"),
            "shares_outstanding": num(ss.get("SharesOutstanding")), "market_cap": num(h.get("MarketCapitalization")),
            "description": g.get("Description"), "website": g.get("WebURL"),
            "employees": num(g.get("FullTimeEmployees")), "isin": g.get("ISIN"), "vendor_metrics": {}})

    def fetch_prices(self, symbol, region):
        start = (date.today() - timedelta(days=365 * 10 + 5)).isoformat()
        d = self._get(f"/eod/{eod_symbol(symbol)}", **{"from": start})
        require(isinstance(d, list) and d, ErrorCode.EMPTY, f"EODHD prices empty for {symbol}")
        bars = [{"d": r["date"], "o": num(r.get("open")), "h": num(r.get("high")), "l": num(r.get("low")),
                 "c": num(r.get("close")), "ac": num(r.get("adjusted_close")) or num(r.get("close")),
                 "v": num(r.get("volume"))} for r in d if num(r.get("close"))]
        divs = []
        try:
            divs = [{"d": r["date"], "amount": num(r.get("value"))}
                    for r in self._get(f"/div/{eod_symbol(symbol)}", **{"from": start}) or [] if num(r.get("value"))]
        except ProviderError:
            pass
        return ProviderResult(route="/eod + /div", payload={
            "quote_currency": None, "adjustment": "EODHD adjusted_close (split + dividend)",
            "bars": bars, "dividends": divs, "splits": []})

    def fetch_financials(self, symbol, region):
        f = self._fund(symbol).get("Financials") or {}
        require(f, ErrorCode.EMPTY, "EODHD Financials block missing")

        def build(kind: str, fp: str):
            inc = (f.get("Income_Statement") or {}).get(kind) or {}
            bs = (f.get("Balance_Sheet") or {}).get(kind) or {}
            cf = (f.get("Cash_Flow") or {}).get(kind) or {}
            out, cur = [], None
            for d in sorted(set(inc) | set(bs) | set(cf), reverse=True)[:12]:
                i, b, c = inc.get(d, {}), bs.get(d, {}), cf.get(d, {})
                cur = cur or i.get("currency_symbol") or b.get("currency_symbol")
                fields = clean_fields({
                    "revenue": i.get("totalRevenue"), "gross_profit": i.get("grossProfit"),
                    "operating_income": i.get("operatingIncome"), "ebit": i.get("ebit") or i.get("operatingIncome"),
                    "ebitda": i.get("ebitda"), "net_income": i.get("netIncomeApplicableToCommonShares") or i.get("netIncome"),
                    "interest_expense": i.get("interestExpense"), "pretax_income": i.get("incomeBeforeTax"),
                    "income_tax": i.get("incomeTaxExpense"), "d_and_a": c.get("depreciation"),
                    "operating_cash_flow": c.get("totalCashFromOperatingActivities"),
                    "capex": c.get("capitalExpenditures"), "sbc": c.get("stockBasedCompensation"),
                    "dividends_paid": c.get("dividendsPaid"),
                    "total_debt": b.get("shortLongTermDebtTotal"), "cash": b.get("cash") or b.get("cashAndEquivalents"),
                    "cash_and_st_investments": b.get("cashAndShortTermInvestments"),
                    "total_equity": b.get("totalStockholderEquity"),
                    "minority_interest": b.get("noncontrollingInterestInConsolidatedEntity"),
                    "preferred_equity": b.get("preferredStockTotalEquity"), "total_assets": b.get("totalAssets"),
                    "lease_liabilities": b.get("capitalLeaseObligations"),
                    "diluted_shares": b.get("commonStockSharesOutstanding")})
                if fields:
                    out.append({"period_end": d, "fiscal_period": fp, "filing_date": i.get("filing_date"), "fields": fields})
            return out, cur

        annual, cur = build("yearly", "FY")
        quarterly, _ = build("quarterly", "Q")
        require(annual, ErrorCode.EMPTY, "EODHD annual statements empty")
        return ProviderResult(route="/fundamentals (Financials)", notes=["EPS derived from net income / shares"],
                              payload={"financial_currency": cur, "annual": annual, "quarterly": quarterly})

    def fetch_estimates(self, symbol, region):
        tr = (self._fund(symbol).get("Earnings") or {}).get("Trend") or {}
        require(tr, ErrorCode.EMPTY, "EODHD Earnings.Trend empty")
        periods = []
        for d, r in sorted(tr.items(), reverse=True)[:4]:
            periods.append({"period": r.get("period"), "end_date": d,
                            "eps_avg": num(r.get("earningsEstimateAvg")), "eps_low": num(r.get("earningsEstimateLow")),
                            "eps_high": num(r.get("earningsEstimateHigh")),
                            "eps_analysts": num(r.get("earningsEstimateNumberOfAnalysts")),
                            "revenue_avg": num(r.get("revenueEstimateAvg")),
                            "revenue_analysts": num(r.get("revenueEstimateNumberOfAnalysts")),
                            "eps_trend": {"current": num(r.get("epsTrendCurrent")), "d7": num(r.get("epsTrend7daysAgo")),
                                          "d30": num(r.get("epsTrend30daysAgo")), "d60": num(r.get("epsTrend60daysAgo")),
                                          "d90": num(r.get("epsTrend90daysAgo"))}})
        return ProviderResult(route="/fundamentals (Earnings.Trend)", payload={"periods": periods, "basis": "EODHD consensus"})

    def fetch_price_targets(self, symbol, region):
        d = self._fund(symbol)
        tp = num((d.get("AnalystRatings") or {}).get("TargetPrice")) or num((d.get("Highlights") or {}).get("WallStreetTargetPrice"))
        require(tp, ErrorCode.EMPTY, "EODHD target price missing")
        return ProviderResult(route="/fundamentals (AnalystRatings)", status="partial",
                              notes=["Mean target only; no high/low range"],
                              payload={"quote_currency": None, "high": None, "low": None, "mean": tp, "median": None, "analysts": None})

    def fetch_news(self, symbol, region):
        d = self._get("/news", s=eod_symbol(symbol), limit=30)
        require(isinstance(d, list) and d, ErrorCode.EMPTY, "EODHD news empty")
        return ProviderResult(route="/news", payload={"items": [
            {"title": r.get("title"), "url": r.get("link"), "source": "EODHD", "published_at": r.get("date"),
             "summary": (r.get("content") or "")[:400]} for r in d]})

    def fetch_fund_profile(self, symbol, region):
        from .funds import eodhd_fund_profile
        return eodhd_fund_profile(self, symbol)

    def fetch_holdings(self, symbol, region):
        from .funds import eodhd_holdings
        return eodhd_holdings(self, symbol)
