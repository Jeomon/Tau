"""Per-phase startup timing diagnostics.

A minimal stopwatch used by ``tau --startup`` to report how long each phase
of runtime bootstrap took — settings load, model/LLM resolution, session
manager init, resource discovery, extension loading. Disabled by default:
``mark()`` is a no-op unless ``enable()`` was called first, so instrumenting
a call site costs nothing in the common case.

Not thread/task-safe beyond a single sequential startup — Tau's bootstrap is
one straight-line asyncio coroutine, so a module-level timer is sufficient.
"""

from __future__ import annotations

import sys
import time

_enabled = False
_start_time: float | None = None
_marks: list[tuple[str, float]] = []


def enable() -> None:
    """Start (or restart) timing collection from now."""
    global _enabled, _start_time, _marks
    _enabled = True
    _start_time = time.perf_counter()
    _marks = []


def is_enabled() -> bool:
    return _enabled


def mark(label: str) -> None:
    """Record a timing mark for ``label``, elapsed since enable() was called."""
    if not _enabled or _start_time is None:
        return
    _marks.append((label, time.perf_counter() - _start_time))


def report() -> list[tuple[str, float]]:
    """Return recorded marks as (label, elapsed_seconds_since_enable) pairs."""
    return list(_marks)


def print_report(file=None) -> None:
    """Print collected marks to stderr (or ``file``), each phase's own delta and running total."""
    if not _enabled or not _marks:
        return
    out = file or sys.stderr
    print("--- Startup Timings ---", file=out)
    prev = 0.0
    for label, t in _marks:
        delta_ms = (t - prev) * 1000
        total_ms = t * 1000
        print(f"  {label}: {delta_ms:.0f}ms (total {total_ms:.0f}ms)", file=out)
        prev = t
