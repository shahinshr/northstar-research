"""FX reference rates and cash (risk-free) rates.

FX chain     : Bank of Canada Valet -> Frankfurter (ECB) -> Yahoo FX tickers
Cash rates   : CAD = BoC 3m T-bill; USD = FRED DTB3 -> Yahoo ^IRX; GBP = BoE SONIA
All series are stored as {CCY: [{"d": iso, "v": float}]}; FX values are CAD per 1 unit of CCY.
Reference FX is not an executable rate (spec §10).
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from .base import ErrorCode, Provider, ProviderError, ProviderResult, require
from .canonical import num

FX_CCYS = ["USD", "GBP", "EUR", "CHF", "JPY", "AUD", "HKD", "CNY", "SEK", "NOK"]  # all published by BoC Valet


def _start(years: int = 11) -> date:
    return date.today() - timedelta(days=365 * years)


class BankOfCanadaProvider(Provider):
    id = "boc"
    name = "Bank of Canada Valet"
    signup_url = "https://www.bankofcanada.ca/valet-api-how-to/"

    def fetch_fx(self) -> ProviderResult:
        series = ",".join(f"FX{c}CAD" for c in FX_CCYS)
        d = self.get_json(f"https://www.bankofcanada.ca/valet/observations/{series}/json",
                          params={"start_date": _start().isoformat()})
        obs = d.get("observations") or []
        require(obs, ErrorCode.EMPTY, "BoC FX empty")
        out = {c: [] for c in FX_CCYS}
        for o in obs:
            for c in FX_CCYS:
                v = num((o.get(f"FX{c}CAD") or {}).get("v"))
                if v:
                    out[c].append({"d": o["d"], "v": v})
        return ProviderResult(route=f"/valet/observations/{series}", payload={"base": "CAD", "series": out})

    def fetch_cad_cash(self) -> list[dict]:
        d = self.get_json("https://www.bankofcanada.ca/valet/observations/TB.CDN.90D.MID/json",
                          params={"start_date": _start(6).isoformat()})
        return [{"d": o["d"], "v": num(o["TB.CDN.90D.MID"]["v"])} for o in d.get("observations", [])
                if num((o.get("TB.CDN.90D.MID") or {}).get("v")) is not None]


class FrankfurterProvider(Provider):
    id = "frankfurter"
    name = "Frankfurter (ECB reference rates)"
    signup_url = "https://frankfurter.dev"

    def fetch_fx(self) -> ProviderResult:
        syms = ",".join(c for c in FX_CCYS + ["CAD"] if c != "EUR")
        d = self.get_json(f"https://api.frankfurter.dev/v1/{_start().isoformat()}..", params={"base": "EUR", "symbols": syms})
        rates = d.get("rates") or {}
        require(rates, ErrorCode.EMPTY, "Frankfurter empty")
        out = {c: [] for c in FX_CCYS}
        for day, r in sorted(rates.items()):
            cad = r.get("CAD")
            if not cad:
                continue
            out["EUR"].append({"d": day, "v": cad})
            for c in FX_CCYS:
                if c != "EUR" and r.get(c):
                    out[c].append({"d": day, "v": cad / r[c]})
        return ProviderResult(route="/v1/{start}..?base=EUR", payload={"base": "CAD", "series": out})


class YahooFXProvider(Provider):
    id = "yahoo"
    name = "Yahoo FX (unofficial)"

    def fetch_fx(self) -> ProviderResult:
        import yfinance as yf
        out = {}
        for c in FX_CCYS:
            try:
                h = yf.Ticker(f"{c}CAD=X").history(period="10y")
                out[c] = [{"d": ts.date().isoformat(), "v": float(v)} for ts, v in h["Close"].items() if v > 0]
            except Exception:
                out[c] = []
        require(out.get("USD"), ErrorCode.EMPTY, "Yahoo FX empty")
        return ProviderResult(route="yfinance {CCY}CAD=X", status="partial", payload={"base": "CAD", "series": out})


class CashRates(Provider):
    """Collects currency-matched cash-rate series for Sharpe ratios (spec §7)."""
    id = "cash_rates"
    name = "Central-bank cash rates"

    def __init__(self, boc: BankOfCanadaProvider):
        super().__init__("")
        self.boc = boc

    def _fred(self, series: str) -> list[dict]:
        txt = self.get_text("https://fred.stlouisfed.org/graph/fredgraph.csv",
                            params={"id": series, "cosd": _start(6).isoformat()})
        rows = list(csv.reader(io.StringIO(txt)))
        require(len(rows) > 10 and rows[0][0].lower() in ("observation_date", "date"), ErrorCode.SCHEMA_CHANGED, "FRED csv")
        return [{"d": r[0], "v": num(r[1])} for r in rows[1:] if num(r[1]) is not None]

    def _boe(self, series: str) -> list[dict]:
        s = _start(6)
        txt = self.get_text("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
                            params={"csv.x": "yes", "Datefrom": s.strftime("%d/%b/%Y"), "Dateto": "now",
                                    "SeriesCodes": series, "CSVF": "TN", "UsingCodes": "Y"},
                            headers={"User-Agent": "Mozilla/5.0 (research dashboard)"})
        from datetime import datetime
        out = []
        for r in csv.reader(io.StringIO(txt)):
            if len(r) >= 2 and num(r[1]) is not None:
                try:
                    out.append({"d": datetime.strptime(r[0].strip(), "%d %b %Y").date().isoformat(), "v": num(r[1])})
                except ValueError:
                    continue
        require(out, ErrorCode.EMPTY, "BoE empty")
        return out

    def _yahoo_irx(self) -> list[dict]:
        import yfinance as yf
        h = yf.Ticker("^IRX").history(period="6y")
        return [{"d": ts.date().isoformat(), "v": float(v)} for ts, v in h["Close"].items()]

    def fetch_all(self) -> tuple[dict, list[dict]]:
        attempts, series = [], {}
        plan = {
            "CAD": [("boc:TB.CDN.90D.MID", self.boc.fetch_cad_cash)],
            "USD": [("fred:DTB3", lambda: self._fred("DTB3")), ("yahoo:^IRX", self._yahoo_irx)],
            "GBP": [("boe:IUDSOIA", lambda: self._boe("IUDSOIA"))],
        }
        for ccy, chain in plan.items():
            for label, fn in chain:
                try:
                    s = fn()
                    require(s, ErrorCode.EMPTY, "empty")
                    series[ccy] = {"source": label, "unit": "annual % yield", "points": s}
                    attempts.append({"provider": label, "status": "ok"})
                    break
                except ProviderError as e:
                    attempts.append({"provider": label, "status": e.code.value, "error": e.message[:160]})
                except Exception as e:  # noqa: BLE001
                    attempts.append({"provider": label, "status": "UPSTREAM_ERROR", "error": str(e)[:160]})
        return series, attempts
