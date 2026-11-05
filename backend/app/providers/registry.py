"""Provider registry + fallback chains.

For each dataset the configured chain (CHAIN_<DATASET> in .env) is walked in order. A provider that is
not configured / unsupported is skipped instantly. A result must also pass dataset-level quality gates;
a merely *partial* result is held as a fallback while later providers are tried for a fully valid one.
Every attempt is recorded so the UI can show the full provenance trail (spec FR09/FR12).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from ..config import get_settings
from ..services.universe import FUND_BY_SYMBOL
from .base import ErrorCode, Provider, ProviderError, ProviderResult
from .eodhd import EODHDProvider
from .fmp import FMPProvider
from .macro import BankOfCanadaProvider, CashRates, FrankfurterProvider, YahooFXProvider
from .misc import (AlphaVantageProvider, FinnhubProvider, GDELTProvider, GoogleNewsProvider, TwelveDataProvider,
                   YahooRSSProvider)
from .funds import IssuerHoldingsProvider
from .sec_edgar import SECEdgarProvider
from .yahoo import YahooProvider


@lru_cache
def providers() -> dict[str, Provider]:
    s = get_settings()
    return {p.id: p for p in [
        YahooProvider(), FMPProvider(s.fmp_api_key), EODHDProvider(s.eodhd_api_key),
        AlphaVantageProvider(s.alphavantage_api_key), FinnhubProvider(s.finnhub_api_key),
        TwelveDataProvider(s.twelvedata_api_key), SECEdgarProvider(s.sec_user_agent), GDELTProvider(),
        YahooRSSProvider(), GoogleNewsProvider(), IssuerHoldingsProvider(FUND_BY_SYMBOL),
    ]}


@lru_cache
def fx_providers() -> dict[str, Provider]:
    boc = BankOfCanadaProvider()
    return {"boc": boc, "frankfurter": FrankfurterProvider(), "yahoo": YahooFXProvider(), "_cash": CashRates(boc)}


@dataclass
class ChainOutcome:
    dataset: str
    provider: str | None = None
    result: ProviderResult | None = None
    attempts: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None


def _quality_gate(dataset: str, r: ProviderResult) -> tuple[bool, str]:
    """Return (is_fully_valid, reason). Failing gates downgrade to 'partial', they do not discard."""
    p = r.payload or {}
    if dataset == "prices":
        bars = p.get("bars") or []
        if len(bars) < 30:
            return False, f"only {len(bars)} bars"
        last = date.fromisoformat(bars[-1]["d"])
        if last < date.today() - timedelta(days=7):
            return False, f"last bar {last} is stale"
    if dataset == "financials":
        a = p.get("annual") or []
        if not a or not any("revenue" in x["fields"] or "net_income" in x["fields"] for x in a):
            return False, "no revenue/net income"
        if not p.get("financial_currency"):
            return False, "reporting currency unknown"
    if r.status == "partial":
        return False, "; ".join(r.notes) or "provider marked partial"
    return True, ""


def run_chain(dataset: str, symbol: str, region: str | None, name: str | None = None,
              chain: list[str] | None = None) -> ChainOutcome:
    out = ChainOutcome(dataset)
    fallback: tuple[str, ProviderResult] | None = None
    reg = providers()
    for pid in chain or get_settings().chain(dataset):
        p = reg.get(pid)
        if p is None:
            out.attempts.append({"provider": pid, "status": "UNKNOWN_PROVIDER"})
            continue
        t0 = time.monotonic()
        try:
            kw = {"name": name} if pid in ("gdelt", "google_news", "yahoo_rss", "finnhub") and dataset == "news" else {}
            try:
                r = p.fetch(dataset, symbol, region, **kw)
            except ProviderError as e:
                if e.code != ErrorCode.RATE_LIMITED:
                    raise
                time.sleep(15)  # back off once, then retry (spec §19: exponential back-off)
                r = p.fetch(dataset, symbol, region, **kw)
            full, why = _quality_gate(dataset, r)
            ms = int((time.monotonic() - t0) * 1000)
            if full:
                out.attempts.append({"provider": pid, "status": "ok", "ms": ms})
                out.provider, out.result = pid, r
                return out
            out.attempts.append({"provider": pid, "status": "partial", "error": why, "ms": ms})
            r.status = "partial"
            if why not in r.notes:
                r.notes.append(why)
            fallback = fallback or (pid, r)
        except ProviderError as e:
            entry = {"provider": pid, "status": e.code.value, "error": e.message[:200]}
            if e.code not in (ErrorCode.NOT_CONFIGURED, ErrorCode.UNSUPPORTED):
                entry["ms"] = int((time.monotonic() - t0) * 1000)
            out.attempts.append(entry)
        except Exception as e:  # noqa: BLE001 - adapter bug must not kill the chain
            out.attempts.append({"provider": pid, "status": "ADAPTER_ERROR", "error": f"{type(e).__name__}: {e}"[:200]})
    if fallback:
        out.provider, out.result = fallback
    return out


def run_fx_chain() -> ChainOutcome:
    out = ChainOutcome("fx")
    reg = fx_providers()
    for pid in get_settings().chain("fx"):
        p = reg.get(pid)
        if not p:
            continue
        t0 = time.monotonic()
        try:
            r = p.fetch_fx()  # type: ignore[attr-defined]
            out.attempts.append({"provider": pid, "status": "ok", "ms": int((time.monotonic() - t0) * 1000)})
            out.provider, out.result = pid, r
            return out
        except ProviderError as e:
            out.attempts.append({"provider": pid, "status": e.code.value, "error": e.message[:200]})
        except Exception as e:  # noqa: BLE001
            out.attempts.append({"provider": pid, "status": "ADAPTER_ERROR", "error": str(e)[:200]})
    return out
