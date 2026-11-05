"""Financial Modeling Prep — stable API (https://financialmodelingprep.com/stable). Spec §10 field map.

Symbols use the same suffixes as Yahoo (RY.TO, VOD.L). Free key = US only, 250 calls/day;
Premium adds CA + UK. The key is sent as the documented `apikey` header (spec §10).
"""
from __future__ import annotations

from .base import ErrorCode, Provider, ProviderError, ProviderResult, require
from .canonical import clean_fields, num

BASE = "https://financialmodelingprep.com/stable"


class FMPProvider(Provider):
    id = "fmp"
    name = "Financial Modeling Prep"
    requires_key = True
    signup_url = "https://site.financialmodelingprep.com/register"
    min_interval_s = 0.25

    def _get(self, route: str, **params):
        data = self.get_json(f"{BASE}{route}", params=params, headers={"apikey": self.api_key})
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            msg = str(data.get("Error Message") or data.get("error"))
            code = ErrorCode.ENTITLEMENT_REQUIRED if ("plan" in msg.lower() or "subscription" in msg.lower()
                                                     or "premium" in msg.lower()) else ErrorCode.UNAUTHORISED
            raise ProviderError(code, msg[:200])
        return data

    def fetch_profile(self, symbol, region):
        d = self._get("/profile", symbol=symbol)
        require(isinstance(d, list) and d, ErrorCode.NOT_FOUND, f"FMP profile empty for {symbol}")
        p = d[0]
        mc, price = num(p.get("marketCap") or p.get("mktCap")), num(p.get("price"))
        return ProviderResult(route="/profile", payload={
            "symbol": symbol, "name": p.get("companyName"), "exchange": p.get("exchange") or p.get("exchangeFullName"),
            "quote_currency": p.get("currency"), "financial_currency": None,
            "sector": p.get("sector"), "industry": p.get("industry"), "country": p.get("country"),
            "quote_type": "etf" if p.get("isEtf") else ("mutualfund" if p.get("isFund") else "equity"),
            "shares_outstanding": (mc / price) if mc and price else None, "market_cap": mc,
            "description": p.get("description"), "website": p.get("website"),
            "employees": num(p.get("fullTimeEmployees")), "isin": p.get("isin"), "vendor_metrics": {}})

    def fetch_prices(self, symbol, region):
        raw = self._get("/historical-price-eod/full", symbol=symbol)
        adj = self._get("/historical-price-eod/dividend-adjusted", symbol=symbol)
        raw = raw.get("historical", raw) if isinstance(raw, dict) else raw
        adj = adj.get("historical", adj) if isinstance(adj, dict) else adj
        require(isinstance(raw, list) and raw, ErrorCode.EMPTY, f"FMP prices empty for {symbol}")
        adj_map = {r["date"]: num(r.get("adjClose")) for r in (adj or []) if isinstance(r, dict)}
        bars = []
        for r in sorted(raw, key=lambda x: x["date"]):
            c = num(r.get("close"))
            if c and c > 0:
                bars.append({"d": r["date"][:10], "o": num(r.get("open")), "h": num(r.get("high")),
                             "l": num(r.get("low")), "c": c, "ac": adj_map.get(r["date"]) or c, "v": num(r.get("volume"))})
        divs = []
        try:
            for dv in self._get("/dividends", symbol=symbol) or []:
                if num(dv.get("dividend")):
                    divs.append({"d": dv["date"], "amount": num(dv["dividend"])})
        except ProviderError:
            pass
        return ProviderResult(route="/historical-price-eod/full + /dividend-adjusted",
                              status="valid" if adj_map else "partial",
                              notes=[] if adj_map else ["No dividend-adjusted series; total return = price return"],
                              payload={"quote_currency": None, "adjustment": "FMP dividend-adjusted close",
                                       "bars": bars, "dividends": divs, "splits": []})

    def _statements(self, symbol, period):
        inc = self._get("/income-statement", symbol=symbol, period=period, limit=10)
        bs = self._get("/balance-sheet-statement", symbol=symbol, period=period, limit=10)
        cf = self._get("/cash-flow-statement", symbol=symbol, period=period, limit=10)
        by_date: dict[str, dict] = {}
        cur = None
        for r in inc or []:
            cur = cur or r.get("reportedCurrency")
            ebit = num(r.get("ebit")) or num(r.get("operatingIncome"))
            by_date.setdefault(r["date"], {"filing_date": r.get("filingDate"), "f": {}})["f"].update({
                "revenue": r.get("revenue"), "gross_profit": r.get("grossProfit"),
                "operating_income": r.get("operatingIncome"), "ebit": ebit, "ebitda": r.get("ebitda"),
                "net_income": r.get("netIncome"), "eps_diluted": r.get("epsDiluted") or r.get("epsdiluted"),
                "diluted_shares": r.get("weightedAverageShsOutDil"), "interest_expense": r.get("interestExpense"),
                "pretax_income": r.get("incomeBeforeTax"), "income_tax": r.get("incomeTaxExpense")})
        for r in bs or []:
            by_date.setdefault(r["date"], {"filing_date": r.get("filingDate"), "f": {}})["f"].update({
                "total_debt": r.get("totalDebt"), "cash": r.get("cashAndCashEquivalents"),
                "cash_and_st_investments": r.get("cashAndShortTermInvestments"),
                "total_equity": r.get("totalStockholdersEquity"), "minority_interest": r.get("minorityInterest"),
                "preferred_equity": r.get("preferredStock"), "total_assets": r.get("totalAssets"),
                "lease_liabilities": r.get("capitalLeaseObligations")})
        for r in cf or []:
            by_date.setdefault(r["date"], {"filing_date": r.get("filingDate"), "f": {}})["f"].update({
                "operating_cash_flow": r.get("operatingCashFlow"), "capex": r.get("capitalExpenditure"),
                "sbc": r.get("stockBasedCompensation"), "d_and_a": r.get("depreciationAndAmortization"),
                "dividends_paid": r.get("commonDividendsPaid") or r.get("netDividendsPaid") or r.get("dividendsPaid")})
        periods = [{"period_end": d, "fiscal_period": "FY" if period == "annual" else "Q",
                    "filing_date": v["filing_date"], "fields": clean_fields(v["f"])}
                   for d, v in sorted(by_date.items(), reverse=True)]
        return periods, cur

    def fetch_financials(self, symbol, region):
        annual, cur = self._statements(symbol, "annual")
        try:
            quarterly, cur_q = self._statements(symbol, "quarter")
        except ProviderError:
            quarterly, cur_q = [], None
        require(annual, ErrorCode.EMPTY, f"FMP statements empty for {symbol}")
        return ProviderResult(route="/income-statement,/balance-sheet-statement,/cash-flow-statement",
                              payload={"financial_currency": cur or cur_q, "annual": annual, "quarterly": quarterly})

    def fetch_estimates(self, symbol, region):
        d = self._get("/analyst-estimates", symbol=symbol, period="annual", limit=4)
        require(isinstance(d, list) and d, ErrorCode.EMPTY, f"FMP estimates empty for {symbol}")
        periods = []
        for i, r in enumerate(sorted(d, key=lambda x: x["date"])[:3]):
            periods.append({"period": r["date"][:4] + "FY", "end_date": r["date"],
                            "eps_avg": num(r.get("epsAvg")), "eps_low": num(r.get("epsLow")),
                            "eps_high": num(r.get("epsHigh")), "eps_analysts": num(r.get("numAnalystsEps")),
                            "revenue_avg": num(r.get("revenueAvg")), "revenue_analysts": num(r.get("numAnalystsRevenue"))})
        return ProviderResult(route="/analyst-estimates", payload={
            "periods": periods, "basis": "FMP consensus; current snapshot only (no trend history)"},
            notes=["FMP returns a current snapshot; revisions need stored history (spec §6)"])

    def fetch_price_targets(self, symbol, region):
        d = self._get("/price-target-consensus", symbol=symbol)
        require(isinstance(d, list) and d, ErrorCode.EMPTY, f"FMP targets empty for {symbol}")
        r = d[0]
        return ProviderResult(route="/price-target-consensus", payload={
            "quote_currency": None, "high": num(r.get("targetHigh")), "low": num(r.get("targetLow")),
            "mean": num(r.get("targetConsensus")), "median": num(r.get("targetMedian")), "analysts": None})

    def fetch_news(self, symbol, region):
        d = self._get("/news/stock", symbols=symbol, limit=30)
        require(isinstance(d, list) and d, ErrorCode.EMPTY, "FMP news empty")
        return ProviderResult(route="/news/stock", payload={"items": [
            {"title": r.get("title"), "url": r.get("url"), "source": r.get("publisher") or r.get("site"),
             "published_at": r.get("publishedDate"), "summary": (r.get("text") or "")[:400]} for r in d]})

    def fetch_fund_profile(self, symbol, region):
        from .funds import fmp_fund_profile
        return fmp_fund_profile(self, symbol)

    def fetch_holdings(self, symbol, region):
        from .funds import fmp_holdings
        return fmp_holdings(self, symbol)
