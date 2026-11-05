"""In-app alerts (spec §3): new qualifying candidate, significant score deterioration, new earnings release,
watchlist item becoming stale. Generated once per published run by diffing against the previous run."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Alert, AnalysisResult, AnalysisRun, WatchlistItem

SCORE_DROP = 10.0


def _rows(s: Session, run_id: str) -> dict[str, dict]:
    return {r.instrument_id: (r.row or {}) for r in s.scalars(select(AnalysisResult).where(AnalysisResult.run_id == run_id))}


def generate_alerts(s: Session, run: AnalysisRun, prev: AnalysisRun | None) -> int:
    if prev is None:
        return 0
    cur, old = _rows(s, run.id), _rows(s, prev.id)
    watched = {w.instrument_id for w in s.scalars(select(WatchlistItem))}
    made = 0

    def add(iid, kind, msg, sev="info"):
        nonlocal made
        s.add(Alert(run_id=run.id, instrument_id=iid, kind=kind, message=msg, severity=sev))
        made += 1

    for iid, r in cur.items():
        o = old.get(iid) or {}
        sym = r.get("symbol")
        if r.get("asset_class") == "stock":
            if r.get("discovery") and not o.get("discovery") and o:
                add(iid, "new_candidate", f"{sym} newly qualifies: {', '.join(r['discovery'])}")
            a, b = (r.get("core") or {}).get("score"), (o.get("core") or {}).get("score")
            if a is not None and b is not None and b - a >= SCORE_DROP:
                add(iid, "score_drop", f"{sym} core score fell {b:.0f} → {a:.0f}", "warning")
            st_new = ((r.get("confidence") or {}).get("freshness") or {}).get("latest_statement")
            st_old = ((o.get("confidence") or {}).get("freshness") or {}).get("latest_statement")
            if st_new and st_old and st_new > st_old:
                add(iid, "new_earnings", f"{sym} reported a new period ({st_new})")
        if iid in watched:
            fr = (r.get("confidence") or {}).get("freshness") or {}
            ofr = (o.get("confidence") or {}).get("freshness") or {}
            if (fr.get("price_stale") or fr.get("statement_stale")) and not (ofr.get("price_stale") or ofr.get("statement_stale")):
                add(iid, "watch_stale", f"Watchlist item {sym} has stale data", "warning")
    s.commit()
    return made
