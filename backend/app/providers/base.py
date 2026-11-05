"""Provider adapter contract (spec §11).

Every adapter returns *canonical* payloads (see `canonical.py`) or raises a typed ProviderError.
An HTTP 200 carrying an error string or an empty body must raise, never return zeros (spec §11, FR12).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx


class ErrorCode(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"  # no API key set
    UNAUTHORISED = "UNAUTHORISED"
    ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED = "UNSUPPORTED"  # provider does not offer this dataset / market
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    STALE = "STALE"
    EMPTY = "EMPTY"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"


class ProviderError(Exception):
    def __init__(self, code: ErrorCode, message: str = ""):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message


@dataclass
class ProviderResult:
    payload: Any
    route: str
    status: str = "valid"  # valid | partial
    notes: list[str] = field(default_factory=list)


class RateLimiter:
    """Simple per-provider minimum-interval limiter (token bucket of size 1)."""

    def __init__(self, min_interval_s: float):
        self.min_interval = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


DATASETS = ("profile", "prices", "financials", "estimates", "price_targets", "news", "fund_profile", "holdings")


class Provider:
    """Base adapter. Subclasses implement fetch_<dataset>(symbol, instrument) for what they support."""

    id: str = "base"
    name: str = "Base"
    requires_key: bool = False
    signup_url: str = ""
    min_interval_s: float = 0.2
    markets: tuple[str, ...] | None = None  # None = any market; restrict only where a provider genuinely can't serve

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.limiter = RateLimiter(self.min_interval_s)
        self._client: httpx.Client | None = None

    # -- capabilities ----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return (not self.requires_key) or bool(self.api_key)

    def supports(self, dataset: str) -> bool:
        return hasattr(self, f"fetch_{dataset}")

    def capabilities(self) -> dict:
        return {
            "id": self.id, "name": self.name, "configured": self.configured,
            "requires_key": self.requires_key, "signup_url": self.signup_url,
            "markets": list(self.markets) if self.markets else ["all"],
            "datasets": [d for d in DATASETS if self.supports(d)],
        }

    def fetch(self, dataset: str, symbol: str, region: str | None, **kw) -> ProviderResult:
        if not self.configured:
            raise ProviderError(ErrorCode.NOT_CONFIGURED, f"{self.name} API key not set")
        if not self.supports(dataset):
            raise ProviderError(ErrorCode.UNSUPPORTED, f"{self.name} has no {dataset} adapter")
        if region and self.markets is not None and region not in self.markets:
            raise ProviderError(ErrorCode.UNSUPPORTED, f"{self.name} adapter not enabled for {region}")
        return getattr(self, f"fetch_{dataset}")(symbol, region, **kw)

    # -- HTTP helper -------------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30, follow_redirects=True,
                                        headers={"User-Agent": "InvestmentDashboard/0.1"})
        return self._client

    calls: dict[str, int] = {}  # class-level daily call counter {"YYYY-MM-DD|provider": n}

    def count(self, n: int = 1) -> None:
        import datetime as _dt
        k = f"{_dt.date.today().isoformat()}|{self.id}"
        Provider.calls[k] = Provider.calls.get(k, 0) + n

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        self.limiter.wait()
        self.count()
        try:
            r = self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, f"{type(e).__name__}: {e}") from e
        if r.status_code in (401, 403):
            raise ProviderError(ErrorCode.UNAUTHORISED if r.status_code == 401 else ErrorCode.ENTITLEMENT_REQUIRED,
                                r.text[:200])
        if r.status_code == 402:
            raise ProviderError(ErrorCode.ENTITLEMENT_REQUIRED, r.text[:200])
        if r.status_code == 404:
            raise ProviderError(ErrorCode.NOT_FOUND, url)
        if r.status_code == 429:
            raise ProviderError(ErrorCode.RATE_LIMITED, r.text[:200])
        if r.status_code >= 400:
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError(ErrorCode.SCHEMA_CHANGED, f"non-JSON response: {r.text[:120]}") from e

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str:
        self.limiter.wait()
        self.count()
        try:
            r = self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, str(e)) from e
        if r.status_code == 429:
            raise ProviderError(ErrorCode.RATE_LIMITED, "")
        if r.status_code >= 400:
            raise ProviderError(ErrorCode.UPSTREAM_ERROR, f"HTTP {r.status_code}")
        return r.text


def require(cond: Any, code: ErrorCode, msg: str) -> None:
    if not cond:
        raise ProviderError(code, msg)
