"""Freshness without hammering providers (spec §15 "Refresh prices", §19).

* On-view refresh: when someone looks at an instrument whose quote or news is stale, a background task
  refreshes just that instrument (quote + news). It is deduplicated, rate-limited per instrument, and runs
  on its own small pool so it never waits behind a long discovery job.
* Readers never block on providers: the API always answers from the database immediately.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..analytics.market import market_open
from ..db import Instrument, SessionLocal, latest_snapshot, utcnow
from ..providers.registry import run_chain
from .ingest import refresh_quotes, store

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="live")
_lock = threading.Lock()
_last: dict[str, float] = {}
MIN_GAP_S = 300            # at most one on-view refresh per instrument every 5 minutes
QUOTE_STALE = timedelta(minutes=10)
NEWS_STALE = timedelta(minutes=15)


def _age(snap):
    if snap is None:
        return None
    t = snap.fetched_at if snap.fetched_at.tzinfo else snap.fetched_at.replace(tzinfo=utcnow().tzinfo)
    return utcnow() - t


def needs_refresh(s, inst: Instrument) -> dict:
    q_age = _age(latest_snapshot(s, inst.id, "quote"))
    n_age = _age(latest_snapshot(s, inst.id, "news"))
    want_quote = inst.asset_type != "mutualfund" and market_open(inst.region or "US") and (q_age is None or q_age > QUOTE_STALE)
    want_news = inst.asset_type == "stock" and (n_age is None or n_age > NEWS_STALE)
    return {"quote": want_quote, "news": want_news, "quote_age_s": q_age.total_seconds() if q_age else None,
            "news_age_s": n_age.total_seconds() if n_age else None}


def ensure_fresh(iid: str) -> bool:
    """Schedule a background refresh if stale. Returns True if one is running or was scheduled."""
    with SessionLocal() as s:
        inst = s.get(Instrument, iid)
        if inst is None:
            return False
        need = needs_refresh(s, inst)
    if not (need["quote"] or need["news"]):
        return False
    with _lock:
        if time.monotonic() - _last.get(iid, -1e9) < MIN_GAP_S:
            return True
        _last[iid] = time.monotonic()
    _pool.submit(_refresh_one, iid, need["quote"], need["news"])
    return True


def _refresh_one(iid: str, quote: bool, news: bool) -> None:
    try:
        with SessionLocal() as s:
            inst = s.get(Instrument, iid)
            if quote:
                refresh_quotes(s, [inst])
            if news:
                store(s, inst.id, "news", run_chain("news", inst.symbol, inst.region, name=inst.name))
    except Exception:  # noqa: BLE001 - background best effort; the UI keeps showing stored data
        log.exception("on-view refresh failed for %s", iid)
