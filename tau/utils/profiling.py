"""Opt-in component profiling: ``TAU_PROFILE=1`` times instrumented spans,
aggregates per-span-name stats (count/total/min/max/avg), and writes a
summary report to the logs directory at process exit.

Disabled by default: ``span()``/``aspan()`` skip the clock and the lock
entirely unless ``TAU_PROFILE=1`` was set before this module was first
imported, so instrumenting a call site costs nothing in the common case.

Complements ``tau.utils.timing`` rather than replacing it: timing.py is a
sequential stopwatch for ``tau --startup``'s single-shot phase breakdown;
this module aggregates *recurring* spans (session persists, render frames,
tool calls) that fire many times per run, potentially from multiple threads
(session persistence now runs via asyncio.to_thread) or concurrent asyncio
tasks (parallel tool calls) — hence the lock around stat updates.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

_enabled = os.environ.get("TAU_PROFILE") == "1"
_lock = threading.Lock()


@dataclass
class SpanStats:
    count: int = 0
    total: float = 0.0
    min: float = field(default=float("inf"))
    max: float = 0.0


_stats: dict[str, SpanStats] = {}


def is_enabled() -> bool:
    return _enabled


def _record(name: str, elapsed: float) -> None:
    with _lock:
        s = _stats.setdefault(name, SpanStats())
        s.count += 1
        s.total += elapsed
        if elapsed < s.min:
            s.min = elapsed
        if elapsed > s.max:
            s.max = elapsed


@contextmanager
def span(name: str) -> Generator[None]:
    """Time a synchronous block under ``name``. No-op unless TAU_PROFILE=1."""
    if not _enabled:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        _record(name, time.perf_counter() - start)


@asynccontextmanager
async def aspan(name: str):
    """Time an async block under ``name``. No-op unless TAU_PROFILE=1."""
    if not _enabled:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        _record(name, time.perf_counter() - start)


def record_phases(prefix: str, marks: list[tuple[str, float]]) -> None:
    """Fold a ``tau.utils.timing``-style cumulative mark list into spans.

    ``marks`` is ``[(label, elapsed_since_enable), ...]`` in order — the same
    shape ``timing.report()`` returns. Each phase's own delta (this mark's
    time minus the previous one's) is recorded as ``f"{prefix}.{label}"``,
    so a single-shot startup sequence shows up in the same aggregate report
    as recurring spans instead of needing a separate reporting path.
    """
    if not _enabled:
        return
    prev = 0.0
    for label, t in marks:
        _record(f"{prefix}.{label}", max(0.0, t - prev))
        prev = t


def report() -> list[tuple[str, SpanStats]]:
    """Return recorded span stats, sorted by total time descending."""
    with _lock:
        return sorted(
            ((name, SpanStats(s.count, s.total, s.min, s.max)) for name, s in _stats.items()),
            key=lambda kv: kv[1].total,
            reverse=True,
        )


def _write_report() -> None:
    rows = report()
    if not rows:
        return
    from tau.settings.paths import get_logs_dir

    try:
        get_logs_dir().mkdir(parents=True, exist_ok=True)
        path = get_logs_dir() / f"profile-{os.getpid()}-{int(time.time())}.log"
        with path.open("w", encoding="utf-8") as f:
            f.write(
                f"{'span':<44} {'count':>8} {'total_ms':>12} "
                f"{'avg_ms':>10} {'min_ms':>10} {'max_ms':>10}\n"
            )
            for name, s in rows:
                avg = s.total / s.count if s.count else 0.0
                f.write(
                    f"{name:<44} {s.count:>8} {s.total * 1000:>12.1f} "
                    f"{avg * 1000:>10.2f} {s.min * 1000:>10.2f} {s.max * 1000:>10.2f}\n"
                )
    except OSError:
        pass


if _enabled:
    atexit.register(_write_report)
