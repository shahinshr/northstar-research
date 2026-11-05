"""Peer-relative scoring models (spec §4, §6).

Models are data, versioned by id. Scores are relative research ranks 0-100 — NOT expected returns.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

H, L = "higher", "lower"

CORE_V1 = {
    "id": "core_v1", "label": "Core score v1",
    "components": {
        "value": {"weight": 0.30, "inputs": [("fcf_yield", 1 / 3, H), ("earnings_yield", 1 / 3, H), ("ebitda_to_ev", 1 / 3, H)]},
        "quality": {"weight": 0.25, "inputs": [("roic", 0.4, H), ("operating_margin", 0.3, H), ("cash_conversion", 0.3, H)]},
        "growth": {"weight": 0.20, "inputs": [("revenue_growth", 0.5, H), ("eps_growth", 0.5, H)]},
        "resilience": {"weight": 0.15, "inputs": [("net_debt_ebitda", 0.6, L), ("interest_coverage", 0.4, H)]},
        "momentum": {"weight": 0.10, "inputs": [("momentum_12_1", 1.0, H)]},
    },
}
ESTIMATE_V1 = {
    "id": "estimate_enhanced_v1", "label": "Estimate-enhanced score v1 (provisional)",
    "components": {
        "value": {"weight": 0.30, "inputs": CORE_V1["components"]["value"]["inputs"]},
        "quality": {"weight": 0.25, "inputs": CORE_V1["components"]["quality"]["inputs"]},
        "growth": {"weight": 0.15, "inputs": CORE_V1["components"]["growth"]["inputs"]},
        "resilience": {"weight": 0.15, "inputs": CORE_V1["components"]["resilience"]["inputs"]},
        "momentum": {"weight": 0.05, "inputs": CORE_V1["components"]["momentum"]["inputs"]},
        "revisions": {"weight": 0.10, "inputs": [("eps_revision", 1.0, H)]},
    },
}
ATTENTION_V1 = {"id": "attention_v1", "label": "Attention v1",
                "inputs": [("relative_return_6m", 0.40, H), ("abnormal_volume", 0.35, H), ("news_activity", 0.25, H)]}
ATTENTION_PV_V1 = {"id": "attention_pv_v1", "label": "Price-and-volume attention v1",
                   "inputs": [("relative_return_6m", 0.40 / 0.75, H), ("abnormal_volume", 0.35 / 0.75, H)]}

MIN_TOTAL_COVERAGE = 0.80
MIN_COMPONENT_COVERAGE = 0.50
PEER_LEVELS = ("industry", "sector", "region", "universe")


def percentile_ranks(values: dict[str, float], direction: str) -> dict[str, float]:
    """Winsorise at 2/98th pct, mid-rank ties, map to (0,100). Lower-is-better reversed."""
    if not values:
        return {}
    s = pd.Series(values, dtype=float)
    if len(s) >= 3:
        lo, hi = np.percentile(s, 2), np.percentile(s, 98)
        s = s.clip(lo, hi)
    n = len(s)
    if n == 1:
        return {s.index[0]: 50.0}
    ranks = s.rank(method="average")
    pct = (ranks - 0.5) / n * 100
    if direction == L:
        pct = 100 - pct
    return pct.round(2).to_dict()


def metric_value(metrics: dict, key: str) -> float | None:
    m = metrics.get(key)
    return m["value"] if m and m.get("status") == "valid" and m.get("value") is not None else None


def peer_percentiles(subjects: dict[str, dict], metric_keys: list[tuple[str, str]], min_size: int,
                     levels: tuple[str, ...] = PEER_LEVELS) -> dict[str, dict]:
    """subjects: id -> {"metrics":{}, <level keys>}. Narrowest group with ≥ min_size valid members wins
    (spec §6 fallback industry → sector → region → universe). Returns id -> metric -> {pct, level, n}."""
    out: dict[str, dict] = {sid: {} for sid in subjects}
    for key, direction in metric_keys:
        vals = {sid: v for sid, s in subjects.items() if (v := metric_value(s["metrics"], key)) is not None}
        for sid in vals:
            chosen = None
            for level in levels:
                grp_key = subjects[sid].get(level) if level != "universe" else "all"
                if grp_key is None:
                    continue
                grp = {o: v for o, v in vals.items()
                       if level == "universe" or subjects[o].get(level) == grp_key}
                if len(grp) >= min_size or level == "universe":
                    chosen = (level, grp_key, grp)
                    break
            level, grp_key, grp = chosen
            pct = percentile_ranks(grp, direction)[sid]
            out[sid][key] = {"pct": pct, "level": level, "group": grp_key, "n": len(grp),
                             "below_min": len(grp) < min_size}
    return out


def score_model(model: dict, metrics: dict, pcts: dict) -> dict[str, Any]:
    comps, coverage, failures = {}, 0.0, []
    for cname, c in model["components"].items():
        planned = sum(w for _, w, _ in c["inputs"])
        avail = [(k, w, d) for k, w, d in c["inputs"] if k in pcts]
        share = sum(w for _, w, _ in avail) / planned
        coverage += c["weight"] * share
        inputs = []
        for k, w, d in c["inputs"]:
            p = pcts.get(k)
            m = metrics.get(k) or {}
            inputs.append({"metric": k, "planned_weight": round(w, 4), "direction": d,
                           "raw": m.get("value"), "status": m.get("status", "na"), "reason": m.get("reason"),
                           "basis": m.get("basis"), "percentile": p["pct"] if p else None,
                           "peer_level": p["level"] if p else None, "peer_n": p["n"] if p else None,
                           "effective_weight": round(w / sum(x[1] for x in avail), 4) if p and avail else 0})
        if share >= MIN_COMPONENT_COVERAGE and avail:
            score = sum(pcts[k]["pct"] * w for k, w, _ in avail) / sum(w for _, w, _ in avail)
            comps[cname] = {"score": round(score, 1), "weight": c["weight"], "coverage": round(share, 3), "inputs": inputs}
        else:
            comps[cname] = {"score": None, "weight": c["weight"], "coverage": round(share, 3), "inputs": inputs}
            failures.append(f"{cname} has {share:.0%} of planned inputs (< 50%)")
    if coverage < MIN_TOTAL_COVERAGE:
        failures.append(f"total input coverage {coverage:.0%} (< 80%)")
    total = None
    if not failures:
        total = round(sum(c["score"] * c["weight"] for c in comps.values()), 1)
    return {"model": model["id"], "label": model["label"], "score": total, "coverage": round(coverage, 3),
            "components": comps, "status": "scored" if total is not None else "insufficient_data", "reasons": failures}


def score_attention(model: dict, pcts: dict) -> dict[str, Any]:
    avail = [(k, w) for k, w, _ in model["inputs"] if k in pcts]
    if len(avail) < len(model["inputs"]):
        return {"model": model["id"], "score": None, "status": "insufficient_data",
                "reasons": [f"missing {', '.join(k for k, _, _ in model['inputs'] if k not in pcts)}"]}
    return {"model": model["id"], "label": model["label"],
            "score": round(sum(pcts[k]["pct"] * w for k, w in avail) / sum(w for _, w in avail), 1),
            "status": "scored", "inputs": [{"metric": k, "weight": round(w, 3), "percentile": pcts[k]["pct"]} for k, w in avail]}


def all_metric_keys(*models: dict) -> list[tuple[str, str]]:
    keys = {}
    for m in models:
        if "components" in m:
            for c in m["components"].values():
                for k, _, d in c["inputs"]:
                    keys[k] = d
        else:
            for k, _, d in m["inputs"]:
                keys[k] = d
    return list(keys.items())


# ----------------------------------------------------------------------------------------- funds (spec §7)
# Full passive model: only when tracking data exists. Otherwise the clearly named basic screen applies.
PASSIVE_EQUITY_V1 = {
    "id": "passive_equity_v1", "label": "Passive fund score v1",
    "components": {
        "cost": {"weight": 0.40, "inputs": [("expense_ratio", 1.0, L)]},
        "tracking": {"weight": 0.30, "inputs": [("tracking_difference", 0.6, H), ("tracking_error", 0.4, L)]},
        "diversification": {"weight": 0.20, "inputs": [("top10_weight", 0.5, L), ("max_sector_weight", 0.5, L)]},
        "liquidity": {"weight": 0.10, "inputs": [("aum_usd", 1.0, H)]},
    },
}
BASIC_FUND_SCREEN_V1 = {
    "id": "basic_fund_screen_v1", "label": "Basic fund screen v1 (no tracking data)",
    "components": {
        "cost": {"weight": 0.50, "inputs": [("expense_ratio", 1.0, L)]},
        "diversification": {"weight": 0.20, "inputs": [("top10_weight", 0.5, L), ("max_sector_weight", 0.5, L)]},
        "scale": {"weight": 0.15, "inputs": [("aum_usd", 1.0, H)]},
        "risk": {"weight": 0.15, "inputs": [("max_drawdown_3y", 1.0, H)]},
    },
}
FUND_PEER_LEVELS = ("cohort", "family", "universe")
