"""Resize repaint cost: drag coalescing and the Termux height-change exemption.

Two independent guards against a resize repaint being more expensive than it
needs to be. A resize frame is the most expensive one the renderer produces —
full clear, full rewrap, full transcript replay — so the paths that decide
*how often* it runs, and *whether* it needs to run at all, both matter.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tau.tui.service import TUI


class FakeTerminal:
    """Minimal stand-in for tau.tui.terminal.Terminal, capturing writes."""

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []
        self.resize_callbacks: list = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback):
        self.resize_callbacks.append(callback)

        def unsubscribe() -> None:
            self.resize_callbacks.remove(callback)

        return unsubscribe

    def fire_resize(self) -> None:
        for cb in list(self.resize_callbacks):
            cb()

    def __getattr__(self, name):  # tolerate the rest of the Terminal surface
        return lambda *a, **k: ""


def _cleared(term: FakeTerminal) -> bool:
    return any("\x1b[2J" in w for w in term.writes)


# ---------------------------------------------------------------------------
# Termux: a height-only change is the soft keyboard, not a reflow
# ---------------------------------------------------------------------------


def _run(coro_fn) -> None:
    """TUI timers need a running loop."""

    async def _wrapped() -> None:
        await coro_fn()

    asyncio.run(_wrapped())


@pytest.fixture
def tui_and_term(monkeypatch):
    term = FakeTerminal()
    tui = TUI(terminal=term)  # type: ignore[arg-type]
    painted: list[None] = []
    monkeypatch.setattr(tui, "_do_render", lambda: painted.append(None))
    return tui, term, painted


def test_first_resize_paints_immediately(tui_and_term):
    """A delayed first paint leaves the reflowed terminal showing stale content."""
    tui, term, painted = tui_and_term

    async def body() -> None:
        tui._on_terminal_resize()
        assert len(painted) == 1  # synchronous, no await needed

    _run(body)


def test_drag_burst_coalesces_into_one_trailing_paint(tui_and_term):
    """A click-drag emits SIGWINCH far faster than one frame interval.

    Real clock rather than a patched one: ``tau.tui.service.time`` *is* the
    stdlib module, so monkeypatching monotonic on it also moves asyncio's own
    clock and the event loop stops making progress. 50 no-op calls land well
    inside the 1/60s interval on any machine that can run the suite at all.
    """
    tui, term, painted = tui_and_term

    async def body() -> None:
        tui._last_render_at = time.monotonic()  # a frame just painted

        for _ in range(50):  # burst inside one frame interval
            tui._on_terminal_resize()
        assert painted == []  # all coalesced, none forced
        assert tui._resize_timer is not None

        await asyncio.sleep(0.05)  # let the scheduled tail fire
        assert len(painted) == 1  # exactly one trailing paint

    _run(body)


def test_coalesced_tail_is_still_painted(tui_and_term, monkeypatch):
    """Nothing may be dropped: the gesture must settle on a correct frame."""
    tui, term, painted = tui_and_term

    async def body() -> None:
        tui._last_render_at = time.monotonic()
        tui._on_terminal_resize()  # inside the interval -> scheduled
        assert painted == []
        assert tui._resize_timer is not None

        await asyncio.sleep(0.05)
        assert len(painted) == 1
        assert tui._resize_timer is None

    _run(body)


def test_cancel_timers_drops_pending_resize(tui_and_term):
    """Teardown must not leave a timer pointing at a disposed renderer."""
    tui, term, painted = tui_and_term

    async def body() -> None:
        tui._last_render_at = time.monotonic()
        tui._on_terminal_resize()
        assert tui._resize_timer is not None

        tui._cancel_timers()
        assert tui._resize_timer is None

        await asyncio.sleep(0.05)
        assert painted == []

    _run(body)
