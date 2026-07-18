"""Regression test: run_bounded_lines() must cancel() *and* await every
losing task in its read/signal race, not just cancel() it.

The read/signal race in run_bounded_lines() (builtins/tools/utils.py) used to
cancel() the losing task without awaiting it. cancel() only *schedules*
cancellation — the task isn't actually done() until the event loop processes
that callback. Whether that happens before run_bounded_lines() returns is a
timing race, not a guarantee: an unawaited cancelled task is what produces
"Task was destroyed but it is pending!" once garbage collected. Asserting on
task.done() at some point after return can't reliably tell "the fix awaited
it" apart from "the loop happened to get around to it anyway" — enough
subsequent event-loop iterations resolve it either way. So this test instead
pins the actual code-level guarantee: every waiter created for the race is
passed through asyncio.gather() before run_bounded_lines() returns, exactly
matching the read loop in builtins/tools/terminal.py this was missing
relative to.
"""

from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock

import pytest

from tau.builtins.tools.utils import run_bounded_lines


@pytest.mark.asyncio
async def test_signal_win_awaits_every_waiter_via_gather() -> None:
    gather_calls: list[tuple[asyncio.Task, ...]] = []
    real_gather = asyncio.gather

    def _tracking_gather(*aws, **kwargs):
        gather_calls.append(aws)
        return real_gather(*aws, **kwargs)

    signal = asyncio.Event()

    # Never writes to stdout, so its readline() can only ever resolve via
    # cancellation — guarantees the signal wins the race deterministically.
    command = [sys.executable, "-c", "import time; time.sleep(5)"]

    async def _trip_signal_soon() -> None:
        await asyncio.sleep(0.02)
        signal.set()

    tripper = asyncio.ensure_future(_trip_signal_soon())
    with mock.patch("asyncio.gather", side_effect=_tracking_gather):
        _exit_code, lines, cancelled = await run_bounded_lines(
            command, max_lines=1000, signal=signal
        )
    await tripper

    assert cancelled is True
    assert lines == []

    assert gather_calls, (
        "run_bounded_lines() never called asyncio.gather() on its read/signal "
        "waiters — it must cancel() *and* await every one of them before "
        "returning, not just cancel() the loser and leave it for the GC"
    )
    # At least one gather() call must have covered both the read task and the
    # signal task from the same race — not just the signal task alone, which
    # would still leave the never-completing read task dangling.
    assert any(len(call) >= 2 for call in gather_calls), (
        f"expected a gather() call covering both waiters from the race, got: {gather_calls}"
    )
