"""Fund holdings & fund-profile adapters beyond Yahoo.

* IssuerHoldingsProvider — complete holdings straight from the fund issuer (official, free):
    ssga:<ticker>                  SPDR daily holdings .xlsx
    blackrock_ca:<id>/<slug>       iShares Canada holdings .csv
  Tickers are mapped to Yahoo-style symbols (RY -> RY.TO) so holdings can be joined to the stock universe
  for look-through valuation and overlap.
* FMP / EODHD fund adapters (keyed backups, spec §10 field map).
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from .base import ErrorCode, Provider, ProviderResult, require
from .canonical import num
from .eodhd import eod_symbol

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
EXCH_SUFFIX = {"toronto stock exchange": ".TO", "tsx venture exchange": ".V", "london stock exchange": ".L",
               "new york stock exchange inc.": "", "nasdaq": "", "nyse": "", "nyse arca": "", "cboe bzx": ""}


def yahoo_symbol(ticker: str, exchange: str | None) -> str:
    t = str(ticker).strip().upper().replace(" ", "-").replace(".", "-")
    if not exchange:
        return t
    ex = exchange.strip().lower()
    for k, suf in EXCH_SUFFIX.items():
        if ex.startswith(k):
            return t + suf
    return t


class IssuerHoldingsProvider(Provider):
    id = "issuer"
    name = "Fund issuer holdings files (SSGA, iShares Canada)"
    signup_url = ""
    min_interval_s = 1.0

    def __init__(self, catalogue: dict[str, dict]):
        super().__init__("")
        self.catalogue = catalogue

    def fetch_holdings(self, symbol, region):
        src = (self.catalogue.get(symbol) or {}).get("holdings")
        require(src, ErrorCode.UNSUPPORTED, "no issuer holdings file mapped for this fund")
        kind, ref = src.split(":", 1)
        if kind == "ssga":
            return self._ssga(ref)
        if kind == "blackrock_ca":
            return self._blackrock_ca(symbol, ref)
        raise_unsupported = ErrorCode.UNSUPPORTED
        require(False, raise_unsupported, f"unknown issuer source {kind}")

    def _ssga(self, ticker: str) -> ProviderResult:
        url = f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker}.xlsx"
        self.limiter.wait()
        r = self.client.get(url, headers=UA)
        require(r.status_code == 200 and "spreadsheet" in r.headers.get("content-type", ""), ErrorCode.UPSTREAM_ERROR,
                f"SSGA returned {r.status_code}")
        raw = pd.read_excel(io.BytesIO(r.content), header=None)
        as_of = None
        for v in raw.iloc[:5, 1].astype(str):
            m = re.search(r"As of (\d{2}-\w{3}-\d{4})", v)
            if m:
                as_of = datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat()
        hdr = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "Name"][0]
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr]]
        df = df[df["Ticker"].notna() & df["Weight"].notna()]
        rows = [{"symbol": yahoo_symbol(r["Ticker"], None), "name": r["Name"], "weight": float(r["Weight"]) / 100,
                 "isin": None, "sector": r.get("Sector"), "asset_class": "Equity", "location": None}
                for _, r in df.iterrows() if num(r["Weight"])]
        require(len(rows) > 20, ErrorCode.SCHEMA_CHANGED, "SSGA file parsed too few rows")
        return ProviderResult(route=f"ssga holdings-daily-us-en-{ticker}.xlsx", payload={
            "complete": True, "as_of": as_of, "holdings": rows, "covered_weight": sum(x["weight"] for x in rows)})

    def _blackrock_ca(self, symbol: str, ref: str) -> ProviderResult:
        pid, slug = ref.split("/", 1)
        tk = symbol.split(".")[0]
        url = (f"https://www.blackrock.com/ca/investors/en/products/{pid}/{slug}/1464253357814.ajax"
               f"?fileType=csv&fileName={tk}_holdings&dataType=fund")
        self.limiter.wait()
        r = self.client.get(url, headers=UA)
        require(r.status_code == 200 and "Ticker," in r.text, ErrorCode.UPSTREAM_ERROR, f"BlackRock returned {r.status_code}")
        lines = r.text.lstrip("﻿").splitlines()
        m = re.search(r'"(\w{3} \d{1,2}, \d{4})"', lines[0])
        as_of = datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat() if m else None
        start = next(i for i, ln in enumerate(lines) if ln.startswith("Ticker,"))
        df = pd.read_csv(io.StringIO("\n".join(lines[start:])), thousands=",")
        df = df[pd.to_numeric(df["Weight (%)"], errors="coerce").notna()]
        rows = [{"symbol": yahoo_symbol(r["Ticker"], r.get("Exchange")), "name": r["Name"],
                 "weight": float(r["Weight (%)"]) / 100, "isin": None, "sector": r.get("Sector"),
                 "asset_class": r.get("Asset Class"), "location": r.get("Location")} for _, r in df.iterrows()]
        require(len(rows) > 10, ErrorCode.SCHEMA_CHANGED, "BlackRock file parsed too few rows")
        return ProviderResult(route=f"blackrock.com/ca …/{tk}_holdings.csv", payload={
            "complete": True, "as_of": as_of, "holdings": rows, "covered_weight": sum(x["weight"] for x in rows)})


def fmp_fund_profile(p, symbol):
    d = p._get("/etf/info", symbol=symbol)
    require(isinstance(d, list) and d, ErrorCode.NOT_FOUND, "FMP etf/info empty")
    r = d[0]
    er = num(r.get("expenseRatio"))
    return ProviderResult(route="/etf/info", status="partial", notes=["No bond/sector detail from this route"], payload={
        "name": r.get("name"), "quote_type": "etf", "family": r.get("etfCompany"), "category": r.get("assetClass"),
        "currency": r.get("navCurrency"), "expense_ratio": er / 100 if er and er > 0.05 else er,
        "expense_ratio_source": "FMP etf/info", "aum": num(r.get("assetsUnderManagement")), "inception": r.get("inceptionDate"),
        "distribution_yield": None, "nav": num(r.get("nav")), "asset_classes": {}, "sector_weights": {},
        "vendor_valuation": {}, "bond": {}})


def fmp_holdings(p, symbol):
    d = p._get("/etf/holdings", symbol=symbol)
    require(isinstance(d, list) and d, ErrorCode.EMPTY, "FMP etf/holdings empty")
    rows = [{"symbol": r.get("asset"), "name": r.get("name"), "weight": (num(r.get("weightPercentage")) or 0) / 100,
             "isin": r.get("isin"), "sector": None, "asset_class": None, "location": None} for r in d]
    return ProviderResult(route="/etf/holdings", payload={"complete": len(rows) > 25, "as_of": d[0].get("updatedAt"),
                                                          "holdings": rows, "covered_weight": sum(x["weight"] for x in rows)})


def eodhd_fund_profile(p, symbol):
    d = p._fund(symbol)
    e = d.get("ETF_Data") or d.get("MutualFund_Data") or {}
    require(e, ErrorCode.UNSUPPORTED, "EODHD: no ETF/MutualFund data block")
    er = num(e.get("NetExpenseRatio") or e.get("Expense_Ratio"))
    return ProviderResult(route=f"/fundamentals/{eod_symbol(symbol)} (ETF_Data)", status="partial", payload={
        "name": (d.get("General") or {}).get("Name"), "quote_type": "etf" if "ETF_Data" in d else "mutualfund",
        "family": e.get("Company_Name"), "category": (d.get("General") or {}).get("Category"),
        "currency": (d.get("General") or {}).get("CurrencyCode"), "expense_ratio": er / 100 if er and er > 0.05 else er,
        "expense_ratio_source": "EODHD", "aum": num(e.get("TotalAssets") or e.get("Portfolio_Net_Assets")),
        "inception": e.get("Inception_Date"), "distribution_yield": num(e.get("Yield")), "nav": num(e.get("Nav")),
        "asset_classes": {}, "sector_weights": {}, "vendor_valuation": {}, "bond": {}})


def eodhd_holdings(p, symbol):
    d = p._fund(symbol)
    e = d.get("ETF_Data") or d.get("MutualFund_Data") or {}
    h = e.get("Holdings") or e.get("Top_Holdings") or {}
    rows = [{"symbol": k, "name": v.get("Name"), "weight": (num(v.get("Assets_%")) or 0) / 100, "isin": None,
             "sector": v.get("Sector"), "asset_class": None, "location": v.get("Country")} for k, v in h.items()]
    require(rows, ErrorCode.EMPTY, "EODHD holdings empty")
    return ProviderResult(route="/fundamentals (Holdings)", payload={"complete": len(rows) > 25, "as_of": None,
                                                                     "holdings": rows, "covered_weight": sum(x["weight"] for x in rows)})
