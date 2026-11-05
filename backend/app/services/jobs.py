"""Background jobs (spec §15/§16): tracked, deduplicated, persisted, survive browser reloads.

Draft uses an in-process thread pool; the job contract matches what an SQS worker would consume later.
Kinds: refresh_prices | refresh_analysis | discovery | recalculate
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..db import Instrument, Job, SessionLocal, get_setting, utcnow
from .analysis import compute_run
from .ingest import prune, refresh_macro, refresh_many, sync_universe

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")  # serial: protects free-tier quotas
_lock = threading.Lock()
ACTIVE = ("queued", "fetching", "calculating")
COOLDOWN = timedelta(seconds=20)


def _update(job_id: str, **kw) -> None:
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        for k, v in kw.items():
            setattr(j, k, v)
        j.updated_at = utcnow()
        s.commit()


def submit(kind: str, instrument_ids: list[str] | None = None, datasets: list[str] | None = None,
           mode: str = "if_stale") -> tuple[Job, bool]:
    """Returns (job, deduplicated). Identical active (or just-finished) jobs are joined, not duplicated."""
    scope_key = f"{kind}:{','.join(sorted(instrument_ids or ['*']))}:{','.join(sorted(datasets or ['*']))}:{mode}"
    with _lock, SessionLocal() as s:
        existing = s.scalars(select(Job).where(Job.scope_key == scope_key).order_by(Job.created_at.desc()).limit(1)).first()
        if existing:
            upd = existing.updated_at if existing.updated_at.tzinfo else existing.updated_at.replace(tzinfo=utcnow().tzinfo)
            if existing.status in ACTIVE or (existing.status == "completed" and utcnow() - upd < COOLDOWN):
                return existing, True
        job = Job(kind=kind, scope_key=scope_key, status="queued",
                  progress={"requested": 0, "done": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0})
        s.add(job)
        s.commit()
        job_id = job.id
    _pool.submit(_run, job_id, kind, instrument_ids, datasets, mode)
    return job, False


def _run(job_id: str, kind: str, ids: list[str] | None, datasets: list[str] | None, mode: str) -> None:
    try:
        with SessionLocal() as s:
            if kind == "validation":
                _update(job_id, status="calculating", message="Running cross-provider checks and walk-forward backtest")
                from ..validation.run import run_validation
                summary = run_validation(lambda msg: _update(job_id, message=msg))
                _update(job_id, status="completed", message=summary)
                return
            if kind in ("refresh_prices", "refresh_analysis", "discovery"):
                prog: dict = {"requested": 0, "done": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0}
                _update(job_id, status="fetching", progress=prog, message="Syncing universe & macro series")
                if kind == "discovery":
                    prog["universe"] = sync_universe(s, mode)
                prog["macro"] = refresh_macro(s, mode)
                if ids:
                    insts = [i for i in (s.get(Instrument, x) for x in ids) if i]
                else:
                    insts = s.scalars(select(Instrument).where(Instrument.active.is_(True))).all()
                ds = datasets or (["prices", "profile"] if kind == "refresh_prices" else None)
                extra = {k: prog[k] for k in ("universe", "macro") if k in prog}

                def cb(t: dict) -> None:
                    _update(job_id, progress={**t, **extra})
                tot = refresh_many(insts, ds, mode, cb)
                prog = {**tot, **extra}
                msg = ("No newer source data available" if tot["updated"] == 0 and tot["failed"] == 0 else
                       f"{tot['updated']} datasets updated, {tot['failed']} failed, {tot['skipped']} fresh")
                _update(job_id, status="calculating", progress=prog, message=msg)
            else:
                _update(job_id, status="calculating", message="Recalculating from stored data (no provider traffic)")
            run = compute_run(s)
            final = "completed" if run.status == "completed" else "failed"
            with SessionLocal() as s2:
                j = s2.get(Job, job_id)
                if final == "completed" and j.progress and j.progress.get("failed"):
                    final = "partial"
                if run.status != "completed":
                    j.message = (run.manifest or {}).get("error", "analysis failed")[:500]
                    s2.commit()
            _update(job_id, status=final, run_id=run.id)
            try:
                removed = prune(s)
                log.info("pruned %s old snapshots", removed)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        _update(job_id, status="failed", message=f"{type(e).__name__}: {e}"[:500])


def seed_universe() -> None:
    with SessionLocal() as s:
        sync_universe(s, "if_stale")


def recover_interrupted_jobs() -> None:
    """A server restart orphans in-flight jobs; mark them failed so the UI never spins forever."""
    with SessionLocal() as s:
        for j in s.scalars(select(Job).where(Job.status.in_(ACTIVE))).all():
            j.status, j.message = "failed", "Interrupted by server restart"
        s.commit()


class DailyScheduler(threading.Thread):
    """Runs discovery once per day at a wall-clock time in an IANA zone (spec §15, FR11).
    Time and zone are read from Settings on every tick, so changes apply without a restart."""

    def __init__(self, at: str = "04:00", tz: str = "America/Toronto"):
        super().__init__(daemon=True, name="scheduler")
        self.default_at, self.default_tz = at, tz
        hh, mm, zone = self._cfg()
        now = datetime.now(zone)
        self.last_date = now.date() if (now.hour, now.minute) >= (hh, mm) else now.date() - timedelta(days=1)

    def _cfg(self):
        with SessionLocal() as s:
            at = get_setting(s, "discovery_time", self.default_at)
            tz = get_setting(s, "timezone", self.default_tz)
        hh, mm = map(int, at.split(":"))
        return hh, mm, ZoneInfo(tz)

    def run(self) -> None:
        while True:
            try:
                hh, mm, zone = self._cfg()
                now = datetime.now(zone)
                if (now.hour, now.minute) >= (hh, mm) and self.last_date != now.date():
                    log.info("scheduled discovery %s", now)
                    submit("discovery")
                    self.last_date = now.date()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            time.sleep(60)
