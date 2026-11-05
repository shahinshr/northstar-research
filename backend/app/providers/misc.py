"""Smaller backup adapters: Alpha Vantage, Finnhub, Twelve Data, GDELT (news, no key)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .base import ErrorCode, Provider, ProviderError, ProviderResult, require
from .canonical import clean_fields, num, split_symbol


# ------------------------------------------------------------------------------------ Alpha Vantage
class AlphaVantageProvider(Provider):
    """Free key = 25 requests/day. TIME_SERIES_DAILY (raw) is free; adjusted series is premium."""
    id = "alphavantage"
    name = "Alpha Vantage"
    requires_key = True
    signup_url = "https://www.alphavantage.co/support/#api-key"
    min_interval_s = 1.2
    markets = ("US",)  # fundamentals are effectively US-only

    def _get(self, **params):
        params["apikey"] = self.api_key
        d = self.get_json("https://www.alphavantage.co/query", params=params)
        if isinstance(d, dict):
            for k in ("Note", "Information"):
                if k in d:
                    raise ProviderError(ErrorCode.RATE_LIMITED, str(d[k])[:200])
            if "Error Message" in d:
                raise ProviderError(ErrorCode.NOT_FOUND, d["Error Message"][:200])
        return d

    def fetch_prices(self, symbol, region):
        d = self._get(function="TIME_SERIES_DAILY", symbol=symbol, outputsize="full")
        ts = d.get("Time Series (Daily)") or {}
        require(ts, ErrorCode.EMPTY, "Alpha Vantage prices empty")
        bars = [{"d": k, "o": num(v["1. open"]), "h": num(v["2. high"]), "l": num(v["3. low"]),
                 "c": num(v["4. close"]), "ac": num(v["4. close"]), "v": num(v["5. volume"])}
                for k, v in sorted(ts.items())]
        return ProviderResult(route="TIME_SERIES_DAILY", status="partial",
                              notes=["Unadjusted closes: total return excludes dividends; splits not adjusted"],
                              payload={"quote_currency": "USD", "adjustment": "none (raw close)",
                                       "bars": bars, "dividends": [], "splits": []})

    def fetch_financials(self, symbol, region):
        inc = self._get(function="INCOME_STATEMENT", symbol=symbol)
        bs = self._get(function="BALANCE_SHEET", symbol=symbol)
        cf = self._get(function="CASH_FLOW", symbol=symbol)
        cur = None

        def build(key, fp):
            nonlocal cur
            by: dict[str, dict] = {}
            for r in inc.get(key, []):
                cur = cur or r.get("reportedCurrency")
                by.setdefault(r["fiscalDateEnding"], {}).update({
                    "revenue": r.get("totalRevenue"), "gross_profit": r.get("grossProfit"),
                    "operating_income": r.get("operatingIncome"), "ebit": r.get("ebit"), "ebitda": r.get("ebitda"),
                    "net_income": r.get("netIncome"), "interest_expense": r.get("interestExpense"),
                    "pretax_income": r.get("incomeBeforeTax"), "income_tax": r.get("incomeTaxExpense"),
                    "d_and_a": r.get("depreciationAndAmortization")})
            for r in bs.get(key, []):
                by.setdefault(r["fiscalDateEnding"], {}).update({
                    "total_debt": r.get("shortLongTermDebtTotal"),
                    "cash": r.get("cashAndCashEquivalentsAtCarryingValue"),
                    "cash_and_st_investments": r.get("cashAndShortTermInvestments"),
                    "total_equity": r.get("totalShareholderEquity"), "total_assets": r.get("totalAssets"),
                    "diluted_shares": r.get("commonStockSharesOutstanding")})
            for r in cf.get(key, []):
                by.setdefault(r["fiscalDateEnding"], {}).update({
                    "operating_cash_flow": r.get("operatingCashflow"), "capex": r.get("capitalExpenditures"),
                    "dividends_paid": r.get("dividendPayout")})
            return [{"period_end": d, "fiscal_period": fp, "filing_date": None, "fields": clean_fields(v)}
                    for d, v in sorted(by.items(), reverse=True)[:10]]

        annual, quarterly = build("annualReports", "FY"), build("quarterlyReports", "Q")
        require(annual, ErrorCode.EMPTY, "Alpha Vantage statements empty")
        return ProviderResult(route="INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW",
                              notes=["No diluted EPS or SBC fields; EPS derived from net income / shares"],
                              payload={"financial_currency": cur, "annual": annual, "quarterly": quarterly})


# ------------------------------------------------------------------------------------ Finnhub
class FinnhubProvider(Provider):
    """Free key = 60 calls/min: profile, quote, company news (North American focus).
    Candles, estimates and price targets are premium on Finnhub, so not wired here."""
    id = "finnhub"
    name = "Finnhub"
    requires_key = True
    signup_url = "https://finnhub.io/register"
    min_interval_s = 1.05

    def _get(self, route, **params):
        params["token"] = self.api_key
        d = self.get_json(f"https://finnhub.io/api/v1{route}", params=params)
        if isinstance(d, dict) and d.get("error"):
            raise ProviderError(ErrorCode.ENTITLEMENT_REQUIRED, d["error"][:200])
        return d

    def fetch_profile(self, symbol, region):
        p = self._get("/stock/profile2", symbol=symbol)
        require(p and p.get("name"), ErrorCode.NOT_FOUND, f"Finnhub profile empty for {symbol}")
        mc = num(p.get("marketCapitalization"))
        so = num(p.get("shareOutstanding"))
        return ProviderResult(route="/stock/profile2", payload={
            "symbol": symbol, "name": p.get("name"), "exchange": p.get("exchange"),
            "quote_currency": p.get("currency"), "financial_currency": None,
            "sector": p.get("finnhubIndustry"), "industry": p.get("finnhubIndustry"), "country": p.get("country"),
            "quote_type": "equity", "shares_outstanding": so * 1e6 if so else None,
            "market_cap": mc * 1e6 if mc else None, "description": None, "website": p.get("weburl"),
            "employees": None, "isin": p.get("isin"), "vendor_metrics": {}})

    def fetch_news(self, symbol, region, name: str | None = None):
        to = date.today()
        d = self._get("/company-news", symbol=symbol, **{"from": (to - timedelta(days=30)).isoformat(), "to": to.isoformat()})
        require(isinstance(d, list) and d, ErrorCode.EMPTY, "Finnhub news empty")
        raw = [{"title": r.get("headline"), "url": r.get("url"), "source": r.get("source"),
                "published_at": datetime.fromtimestamp(r["datetime"], timezone.utc).isoformat() if r.get("datetime") else None,
                "summary": (r.get("summary") or "")[:400]} for r in d[:100]]
        # Finnhub tags stories that merely mention the ticker in passing; keep ones about the company
        items, dropped = filter_relevant(raw, symbol, name)
        require(len(items) >= MIN_RELEVANT, ErrorCode.EMPTY, f"Only {len(items)}/{len(raw)} Finnhub stories are about the company")
        return ProviderResult(route="/company-news", payload={"items": items[:50]},
                              notes=[f"Relevance filter dropped {dropped} of {len(raw)} stories"] if dropped else [])


# ------------------------------------------------------------------------------------ Twelve Data
class TwelveDataProvider(Provider):
    """Free key = 800 calls/day, 8/min. Daily OHLCV; international exchanges may need paid plan."""
    id = "twelvedata"
    name = "Twelve Data"
    requires_key = True
    signup_url = "https://twelvedata.com/register"
    min_interval_s = 8.0
    EXCH = {"TO": "TSX", "L": "LSE", "V": "TSXV"}

    def fetch_prices(self, symbol, region):
        base, suf = split_symbol(symbol)
        params = {"symbol": base, "interval": "1day", "outputsize": 5000, "apikey": self.api_key, "order": "ASC"}
        if suf in self.EXCH:
            params["exchange"] = self.EXCH[suf]
        d = self.get_json("https://api.twelvedata.com/time_series", params=params)
        if d.get("status") == "error":
            code = ErrorCode.RATE_LIMITED if d.get("code") == 429 else ErrorCode.ENTITLEMENT_REQUIRED
            raise ProviderError(code, d.get("message", "")[:200])
        vals = d.get("values") or []
        require(vals, ErrorCode.EMPTY, "Twelve Data prices empty")
        bars = [{"d": v["datetime"][:10], "o": num(v["open"]), "h": num(v["high"]), "l": num(v["low"]),
                 "c": num(v["close"]), "ac": num(v["close"]), "v": num(v.get("volume"))} for v in vals]
        return ProviderResult(route="/time_series", status="partial",
                              notes=["Split-adjusted only; dividends not reinvested"],
                              payload={"quote_currency": (d.get("meta") or {}).get("currency"),
                                       "adjustment": "split-adjusted close", "bars": bars, "dividends": [], "splits": []})


# ------------------------------------------------------------------------------------ RSS news (no key)
_SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|plc|public limited company|group|ltd|limited|co|"
                       r"company|holdings|n\.?v|s\.?a|ag|se)\b\.?", re.I)


def short_name(name: str | None) -> str | None:
    if not name:
        return None
    n = _SUFFIXES.sub("", name).replace(",", " ").strip(" .")
    return re.sub(r"\s+", " ", n) or name


GENERIC_WORDS = {"canadian", "royal", "american", "national", "general", "first", "united", "international",
                 "british", "global", "the", "new", "bank", "of", "and"}
MIN_RELEVANT = 3


def relevance_terms(symbol: str, name: str | None) -> tuple[list[str], str]:
    """Phrases that identify the company in a headline, plus the bare ticker (matched case-sensitively)."""
    base = symbol.split(".")[0].upper()
    terms: list[str] = []
    sn = short_name(name)
    if sn:
        words = sn.split()
        terms.append(sn.lower())
        if len(words) >= 2:
            terms.append(" ".join(words[:2]).lower())
        if words and len(words[0]) >= 4 and words[0].lower() not in GENERIC_WORDS:
            terms.append(words[0].lower())
    return list(dict.fromkeys(terms)), base


def filter_relevant(items: list[dict], symbol: str, name: str | None) -> tuple[list[dict], int]:
    """Keep stories whose title/summary names the company or its ticker as a whole word.
    Guards against feeds that silently map a foreign ticker to another company (seen: RY.TO -> Rayonier)."""
    terms, base = relevance_terms(symbol, name)
    tick = re.compile(rf"(?<![A-Za-z]){re.escape(base)}(?![A-Za-z])")
    def hit(text: str) -> bool:
        return any(t in text.lower() for t in terms) or bool(tick.search(text))
    kept = []
    for it in items:
        if hit(it.get("title") or ""):
            kept.append({**it, "match": "headline"})
        elif hit(it.get("summary") or ""):
            kept.append({**it, "match": "mention"})
    kept.sort(key=lambda x: x["match"] != "headline")  # stable: headline stories first, date order kept
    return kept, len(items) - len(kept)


def _parse_rss(xml_text: str, default_source: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ProviderError(ErrorCode.SCHEMA_CHANGED, f"RSS parse error: {e}") from e
    items = []
    for it in root.iter("item"):
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat() if pub else None
        except (TypeError, ValueError):
            ts = None
        src = it.find("source")
        items.append({"title": (it.findtext("title") or "").strip(), "url": it.findtext("link"),
                      "source": (src.text if src is not None else None) or default_source,
                      "published_at": ts, "summary": (it.findtext("description") or "")[:300] or None})
    return items


class YahooRSSProvider(Provider):
    """Yahoo Finance ticker-linked headline RSS — separate endpoint from yfinance's news API."""
    id = "yahoo_rss"
    name = "Yahoo Finance RSS (ticker-linked)"
    signup_url = "https://finance.yahoo.com"
    min_interval_s = 1.0

    def fetch_news(self, symbol, region, name: str | None = None):
        txt = self.get_text("https://feeds.finance.yahoo.com/rss/2.0/headline",
                            params={"s": symbol, "region": "US", "lang": "en-US"},
                            headers={"User-Agent": "Mozilla/5.0 (research dashboard)"})
        raw = _parse_rss(txt, "Yahoo Finance")
        require(raw, ErrorCode.EMPTY, "Yahoo RSS returned no items")
        items, dropped = filter_relevant(raw, symbol, name)
        require(len(items) >= MIN_RELEVANT, ErrorCode.EMPTY,
                f"Only {len(items)}/{len(raw)} Yahoo RSS stories mention the company — feed likely mismatched")
        return ProviderResult(route="feeds.finance.yahoo.com/rss/2.0/headline", payload={"items": items},
                              notes=[f"Relevance filter dropped {dropped} of {len(raw)} stories"] if dropped else [])


class GoogleNewsProvider(Provider):
    """Google News RSS search (personal-use feed terms). Query = short company name + ticker."""
    id = "google_news"
    name = "Google News RSS (name + ticker search)"
    signup_url = "https://news.google.com"
    min_interval_s = 2.0

    def fetch_news(self, symbol, region, name: str | None = None):
        loc = {"UK": ("en-GB", "GB"), "CA": ("en-CA", "CA")}.get(region or "US", ("en-US", "US"))
        q = f'"{short_name(name)}" {symbol.split(".")[0]} stock when:30d' if name else f"{symbol} stock when:30d"
        txt = self.get_text("https://news.google.com/rss/search",
                            params={"q": q, "hl": loc[0], "gl": loc[1], "ceid": f"{loc[1]}:en"},
                            headers={"User-Agent": "Mozilla/5.0 (research dashboard)"})
        raw = _parse_rss(txt, "Google News")
        items, dropped = filter_relevant(raw, symbol, name)
        require(items, ErrorCode.EMPTY, f"Google News: no relevant stories ({len(raw)} raw)")
        return ProviderResult(route="news.google.com/rss/search", status="partial",
                              notes=["Search-matched, not ticker-linked"] + ([f"Relevance filter dropped {dropped}"] if dropped else []),
                              payload={"items": items[:50]})


# ------------------------------------------------------------------------------------ GDELT
class GDELTProvider(Provider):
    """Free global news index, no key. Matches by company name -> noisier than ticker-linked feeds,
    so it is the LAST news fallback and is labelled as such."""
    id = "gdelt"
    name = "GDELT (free, name-matched)"
    signup_url = "https://www.gdeltproject.org/"
    min_interval_s = 6.0

    def fetch_news(self, symbol, region, name: str | None = None):
        q = f'"{short_name(name)}"' if name else symbol.split(".")[0]
        d = self.get_json("https://api.gdeltproject.org/api/v2/doc/doc",
                          params={"query": f"{q} sourcelang:english", "mode": "artlist", "format": "json",
                                  "maxrecords": 50, "timespan": "30d", "sort": "datedesc"})
        arts = (d or {}).get("articles") or []
        require(arts, ErrorCode.EMPTY, "GDELT returned no articles")
        items = []
        for a in arts:
            sd = a.get("seendate", "")
            try:
                ts = datetime.strptime(sd, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                ts = None
            items.append({"title": a.get("title"), "url": a.get("url"), "source": a.get("domain"),
                          "published_at": ts, "summary": None})
        items, _ = filter_relevant(items, symbol, name)
        require(items, ErrorCode.EMPTY, "GDELT: no relevant articles")
        return ProviderResult(route="api/v2/doc (artlist, 30d)", status="partial",
                              notes=["Name-matched, not ticker-linked: may include unrelated stories"],
                              payload={"items": items})
