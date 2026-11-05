"""Breaking-news tagging (spec §4, §18: news is untrusted text — classified by a transparent lexicon, never executed).

Each story gets: category tags, severity (critical / high / normal) and a crude polarity. Severity is about
*decision relevance* (would an owner want to know today?), not about price direction.
"""
from __future__ import annotations

import re

RULES: list[tuple[str, str, str, list[str]]] = [  # (tag, severity, polarity, patterns)
    ("Guidance", "critical", "neg", [r"cuts? (its |full[- ]year )?(guidance|forecast|outlook)", r"lowers? (guidance|outlook|forecast)",
                                     r"profit warning", r"warns? (on|of) (profit|earnings|sales)"]),
    ("Guidance", "high", "pos", [r"raises? (guidance|outlook|forecast)", r"boosts? (guidance|outlook)", r"lifts? (guidance|outlook)"]),
    ("Legal & regulatory", "critical", "neg", [r"\bsec\b.*(probe|charges?|investigat)", r"investigation", r"\bprobe\b", r"lawsuit",
                                                r"class action", r"antitrust", r"\bfined?\b", r"indicted|fraud", r"subpoena", r"sanction"]),
    ("Solvency", "critical", "neg", [r"bankrupt", r"chapter 11", r"default(s|ed)? on", r"going concern", r"insolven", r"restructuring support"]),
    ("Trading halt", "critical", "neg", [r"trading halt", r"halted", r"delist"]),
    ("Accounting", "critical", "neg", [r"restate", r"accounting (error|irregular)", r"auditor (resign|quit)", r"material weakness"]),
    ("Safety & recall", "high", "neg", [r"recall", r"outage", r"cyber ?attack|data breach|hack(ed)?\b"]),
    ("Management", "high", "neu", [r"\bceo\b.*(resign|steps? down|depart|ousted|fired|retire)", r"(names|appoints) .*\b(ceo|cfo|chief)",
                                   r"\bcfo\b.*(resign|steps? down|depart)"]),
    ("M&A", "high", "neu", [r"\bacquir(e|es|ing|ition)\b", r"\bmerger\b", r"takeover", r"buyout", r"to buy\b", r"\bbid for\b", r"spin[- ]?off"]),
    ("Earnings", "high", "neu", [r"\bearnings\b", r"quarterly results", r"\bq[1-4]\b.*(results|profit|revenue|eps)", r"(beats?|miss(es)?) (estimates|expectations|forecasts)"]),
    ("Rating change", "high", "neg", [r"downgrad"]),
    ("Rating change", "high", "pos", [r"upgrad"]),
    ("Rating change", "normal", "neu", [r"price target", r"initiat(es|ed) coverage"]),
    ("Capital return", "normal", "pos", [r"buyback|repurchase", r"raises? (its )?dividend", r"special dividend"]),
    ("Capital return", "high", "neg", [r"(cuts?|suspends?|slashes) (its )?dividend"]),
    ("Capital raise", "high", "neg", [r"share (offering|sale|placement)", r"dilut", r"convertible (notes|bond)"]),
    ("Debt financing", "normal", "neu", [r"bond (offering|sale|issue)", r"(debt|notes) offering", r"tranche", r"raises? \$?[\d.]+ ?(billion|bn|million) in (debt|bonds|notes)"]),
    ("Product & contracts", "normal", "pos", [r"wins? (a )?contract", r"approval", r"\bfda\b", r"launch", r"partnership"]),
]
NEG = re.compile(r"\b(plunge|plummet|tumble|slump|sink|sinks|sank|falls?|drops?|slides?|crash|misses|weak|loss)\b", re.I)
POS = re.compile(r"\b(soar|surge|jump|rall(y|ies)|climb|gains?|beats?|record|strong|tops)\b", re.I)
_COMPILED = [(tag, sev, pol, [re.compile(p, re.I) for p in pats]) for tag, sev, pol, pats in RULES]
SEV_RANK = {"critical": 3, "high": 2, "normal": 1}


def classify(title: str | None, summary: str | None = None) -> dict:
    text = f"{title or ''}. {summary or ''}"
    tags, sev, pol = [], "normal", 0
    for tag, s, p, pats in _COMPILED:
        if any(rx.search(text) for rx in pats):
            if tag not in tags:
                tags.append(tag)
            if SEV_RANK[s] > SEV_RANK[sev]:
                sev = s
            pol += {"pos": 1, "neg": -1, "neu": 0}[p]
    head = title or ""
    pol += len(POS.findall(head)) - len(NEG.findall(head))
    return {"tags": tags[:3], "severity": sev, "polarity": "positive" if pol > 0 else "negative" if pol < 0 else "neutral"}


def tag_items(items: list[dict]) -> list[dict]:
    return [{**it, **classify(it.get("title"), it.get("summary"))} for it in items]
