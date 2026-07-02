from __future__ import annotations

import re

from ..blocks import Block
from ..content import clip, non_empty_lines

# Require a clear preference construction, not bare keywords.
_PREF_PATTERNS = [
    re.compile(r"\bprefer(?:s|red|ring)?\s+\w", re.I),
    re.compile(r"\bdon'?t want\b", re.I),
    re.compile(
        r"\balways (?:use|do|run|prefer|keep|make|format|write|add|set|put|prefix|start"
        r"|include|append)\b",
        re.I,
    ),
    re.compile(
        r"\bnever (?:use|do|run|push|commit|write|ignore|add|set|put|remove|delete|include"
        r"|deploy)\b",
        re.I,
    ),
    re.compile(r"\bplease (?:use|avoid|keep|make|don'?t|do not|format|write)\b", re.I),
    re.compile(r"\b(?:style|format|language|naming)\s*[:=]\s*\S", re.I),
]


def extract_preferences(blocks: list[Block]) -> list[str]:
    prefs: list[str] = []
    seen: set[str] = set()

    for b in blocks:
        if b.kind != "user":
            continue
        per_block = 0
        for line in non_empty_lines(b.text):
            trimmed = line.strip()
            if not trimmed or len(trimmed) < 5:
                continue
            if len(trimmed) > 200:
                continue
            if trimmed.endswith("?") or "?..." in trimmed:
                continue
            if not any(p.search(trimmed) for p in _PREF_PATTERNS):
                continue
            clipped = clip(trimmed, 200)
            key = clipped.lower()
            if key in seen:
                continue
            seen.add(key)
            prefs.append(clipped)
            # Cap per user block to avoid pasting long rule lists as many prefs.
            per_block += 1
            if per_block >= 1:
                break

    return prefs[:10]


def dedup_preferences_against_goals(prefs: list[str], goals: list[str]) -> list[str]:
    """Remove preferences that duplicate goals (case-insensitive, trimmed)."""
    goal_set = {g.strip().lower() for g in goals}
    return [p for p in prefs if p.strip().lower() not in goal_set]
