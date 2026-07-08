"""Duration parsing and formatting for /loop arguments."""

from __future__ import annotations

import math
import re
from typing import Optional

ONE_MINUTE = 60
FIFTEEN_MINUTES = 15 * ONE_MINUTE
THREE_DAYS = 3 * 24 * 60 * ONE_MINUTE
DEFAULT_LOOP_INTERVAL = 10 * ONE_MINUTE

_DURATION_SHORT = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_DURATION_WORD = re.compile(
    r"^(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)$", re.IGNORECASE
)


def parse_duration(text: str) -> Optional[float]:
    """Parse '5m', '2h', '30 seconds', etc. into seconds."""
    raw = text.strip().lower()
    if not raw:
        return None

    m = _DURATION_SHORT.match(raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return {"s": n, "m": n * ONE_MINUTE, "h": n * 3600, "d": n * 86400}[unit]

    m = _DURATION_WORD.match(raw)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("sec"):
        return n
    if unit.startswith("min"):
        return n * ONE_MINUTE
    if unit.startswith(("hour", "hr")):
        return n * 3600
    if unit.startswith("day"):
        return n * 86400
    return None


def normalize_duration(seconds: float) -> tuple[float, Optional[str]]:
    """Round up to minute granularity, matching the minimum tick resolution."""
    if seconds <= 0:
        return ONE_MINUTE, "Rounded up to 1m (minimum interval)."
    rounded = math.ceil(seconds / ONE_MINUTE) * ONE_MINUTE
    if rounded != seconds:
        return rounded, f"Rounded to {format_duration(rounded)} (minute granularity)."
    return seconds, None


def format_duration(seconds: float) -> str:
    if seconds % 86400 == 0:
        return f"{int(seconds // 86400)}d"
    if seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // ONE_MINUTE)}m"


def _extract_leading_duration(text: str) -> Optional[tuple[float, str]]:
    tokens = text.strip().split()
    if len(tokens) < 2:
        return None
    max_prefix = min(3, len(tokens) - 1)
    for i in range(1, max_prefix + 1):
        candidate = " ".join(tokens[:i])
        secs = parse_duration(candidate)
        if not secs:
            continue
        prompt = " ".join(tokens[i:]).strip()
        if not prompt:
            continue
        return secs, prompt
    return None


def _extract_trailing_duration(text: str) -> Optional[tuple[float, str]]:
    tokens = text.strip().split()
    if len(tokens) < 2:
        return None
    max_suffix = min(3, len(tokens) - 1)
    for i in range(1, max_suffix + 1):
        candidate = " ".join(tokens[-i:])
        secs = parse_duration(candidate)
        if not secs:
            continue
        prompt = " ".join(tokens[:-i]).strip()
        if not prompt:
            continue
        return secs, prompt
    return None


def parse_loop_args(text: str) -> Optional[dict]:
    """/loop 5m <task>  |  /loop <task> every 2h  |  /loop <task> 5m  |  /loop <task> (default 10m)."""
    raw = text.strip()
    if not raw:
        return None

    leading = _extract_leading_duration(raw)
    if leading:
        secs, prompt = leading
        interval_s, note = normalize_duration(secs)
        return {"prompt": prompt, "interval_s": interval_s, "note": note}

    m = re.match(r"^(.*)\s+every\s+(.+)$", raw, re.IGNORECASE)
    if m:
        prompt = m.group(1).strip()
        secs = parse_duration(m.group(2))
        if prompt and secs:
            interval_s, note = normalize_duration(secs)
            return {"prompt": prompt, "interval_s": interval_s, "note": note}

    trailing = _extract_trailing_duration(raw)
    if trailing:
        secs, prompt = trailing
        interval_s, note = normalize_duration(secs)
        return {"prompt": prompt, "interval_s": interval_s, "note": note}

    return {"prompt": raw, "interval_s": DEFAULT_LOOP_INTERVAL, "note": None}
