"""SEC EDGAR XBRL companyfacts — free, official, US registrants only, annual (10-K) periods.

Used as the last financials fallback and as a validation source (spec §9, S22). Requires a descriptive
User-Agent with contact details (set SEC_USER_AGENT); max 10 requests/second.
"""
from __future__ import annotations

from datetime import date

from .base import ErrorCode, Provider, ProviderResult, require
from .canonical import clean_fields

CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "revenue": [("Revenues", "USD"), ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD"), ("SalesRevenueNet", "USD")],
    "gross_profit": [("GrossProfit", "USD")],
    "operating_income": [("OperatingIncomeLoss", "USD")],
    "net_income": [("NetIncomeLoss", "USD")],
    "eps_diluted": [("EarningsPerShareDiluted", "USD/shares")],
    "diluted_shares": [("WeightedAverageNumberOfDilutedSharesOutstanding", "shares")],
    "interest_expense": [("InterestExpense", "USD"), ("InterestExpenseNonoperating", "USD"), ("InterestExpenseDebt", "USD")],
    "pretax_income": [("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "USD"),
                      ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments", "USD")],
    "income_tax": [("IncomeTaxExpenseBenefit", "USD")],
    "d_and_a": [("DepreciationDepletionAndAmortization", "USD"), ("DepreciationAndAmortization", "USD"), ("DepreciationAmortizationAndAccretionNet", "USD")],
    "operating_cash_flow": [("NetCashProvidedByUsedInOperatingActivities", "USD")],
    "capex": [("PaymentsToAcquirePropertyPlantAndEquipment", "USD"), ("PaymentsToAcquireProductiveAssets", "USD")],
    "sbc": [("ShareBasedCompensation", "USD"), ("AllocatedShareBasedCompensationExpense", "USD")],
    "dividends_paid": [("PaymentsOfDividends", "USD"), ("PaymentsOfDividendsCommonStock", "USD")],
    "cash": [("CashAndCashEquivalentsAtCarryingValue", "USD")],
    "_st_investments": [("MarketableSecuritiesCurrent", "USD"), ("ShortTermInvestments", "USD"), ("AvailableForSaleSecuritiesDebtSecuritiesCurrent", "USD")],
    "_lt_debt": [("LongTermDebt", "USD"), ("LongTermDebtAndCapitalLeaseObligations", "USD")],
    "_lt_debt_nc": [("LongTermDebtNoncurrent", "USD")],
    "_lt_debt_c": [("LongTermDebtCurrent", "USD")],
    "_st_debt": [("CommercialPaper", "USD"), ("ShortTermBorrowings", "USD")],
    "total_equity": [("StockholdersEquity", "USD")],
    "minority_interest": [("MinorityInterest", "USD")],
    "total_assets": [("Assets", "USD")],
}


class SECEdgarProvider(Provider):
    id = "sec_edgar"
    name = "SEC EDGAR (official, US annual)"
    signup_url = "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
    min_interval_s = 0.15
    markets = ("US",)
    _cik_map: dict[str, int] | None = None

    def __init__(self, user_agent: str):
        super().__init__("")
        self.ua = user_agent

    def _cik(self, symbol: str) -> int:
        if SECEdgarProvider._cik_map is None:
            d = self.get_json("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": self.ua})
            SECEdgarProvider._cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}
        cik = SECEdgarProvider._cik_map.get(symbol.upper().replace(".", "-"))
        require(cik, ErrorCode.NOT_FOUND, f"{symbol} is not an SEC registrant")
        return cik

    def fetch_financials(self, symbol, region):
        cik = self._cik(symbol)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        facts = (self.get_json(url, headers={"User-Agent": self.ua}).get("facts") or {}).get("us-gaap") or {}
        require(facts, ErrorCode.EMPTY, "no us-gaap facts")
        cutoff = date.today().year - 7
        by_end: dict[str, dict[str, float]] = {}
        filed_on: dict[str, str] = {}
        for field, options in CONCEPTS.items():
            for concept, unit in options:
                rows = ((facts.get(concept) or {}).get("units") or {}).get(unit) or []
                got = {}
                for r in rows:
                    if r.get("form") not in ("10-K", "10-K/A") or int(r["end"][:4]) < cutoff:
                        continue
                    if "start" in r:  # duration fact -> must be ~1 year
                        days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
                        if not 350 <= days <= 380:
                            continue
                    prev = got.get(r["end"])
                    if prev is None or r["filed"] > prev["filed"]:  # latest restatement wins for latest-state view
                        got[r["end"]] = r
                if got:
                    for end, r in got.items():
                        by_end.setdefault(end, {}).setdefault(field, r["val"])
                        filed_on[end] = max(filed_on.get(end, ""), r["filed"])
                    break
        periods = []
        for end, f in sorted(by_end.items(), reverse=True)[:6]:
            debt = f.pop("_lt_debt", None)
            nc, c = f.pop("_lt_debt_nc", None), f.pop("_lt_debt_c", None)
            if debt is None and (nc is not None or c is not None):
                debt = (nc or 0) + (c or 0)
            st = f.pop("_st_debt", None)
            if debt is not None or st is not None:
                f["total_debt"] = (debt or 0) + (st or 0)
            sti = f.pop("_st_investments", None)
            if f.get("cash") is not None:
                f["cash_and_st_investments"] = f["cash"] + (sti or 0)
            if "operating_income" in f:
                f["ebit"] = f["operating_income"]
                if "d_and_a" in f:
                    f["ebitda"] = f["operating_income"] + f["d_and_a"]
            fields = clean_fields(f)
            if len(fields) >= 5:
                periods.append({"period_end": end, "fiscal_period": "FY", "filing_date": filed_on.get(end), "fields": fields})
        require(periods, ErrorCode.EMPTY, "no usable 10-K periods")
        return ProviderResult(route=f"/api/xbrl/companyfacts/CIK{cik:010d}.json", status="partial",
                              notes=["Annual 10-K only; no quarterly TTM", "Debt = long-term + short-term borrowings (excludes leases)"],
                              payload={"financial_currency": "USD", "annual": periods, "quarterly": []})
