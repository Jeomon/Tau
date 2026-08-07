"""Regression: a resize must never be observed halfway through a frame.

``Terminal._on_resize`` is a SIGWINCH handler, so it runs between two
bytecodes of the main thread — including bytecodes belonging to a render.
One frame reads the terminal size three times (``StringRenderer.render`` for
the component layout, again for the overlay composite width, then
``ScrollbackRenderer._render`` for the diff), and publishing the new size
between those reads lays the frame out at the old width but paints it at the
new one. Rows then overrun the terminal, which soft-wraps each onto an extra
physical row, and every relative cursor move below addresses the wrong row.

So the size is staged in the handler and published from a loop callback,
which cannot interleave with a frame.
"""

from __future__ import annotations

import asyncio

from tau.tui.component import Component
from tau.tui.service import StringRenderer
from tau.tui.terminal import Terminal
from tau.tui.utils import strip_ansi, visible_width


class _CaptureTerminal(Terminal):
    """A real Terminal (so the real resize path runs) that captures output."""

    def __init__(self) -> None:
        super().__init__(async_output=False)
        self.out: list[str] = []

    def write(self, data: str) -> None:
        self.out.append(data)

    def write_flush(self, data: str) -> None:
        self.out.append(data)

    def flush(self) -> None:
        pass


def _tui_with(children: list[Component]):
    from tau.tui.service import TUI

    tui = TUI.__new__(TUI)
    tui.children = list(children)
    tui._child_rows = {}
    tui._stable_rows = 0
    tui._prev_stable_rows = 0
    tui._elided_start = 0
    tui._elided_end = 0
    tui._child_frozen_gen = {}
    tui._child_row_cache = {}
    tui._elide_stable_prefix_for_next_render = False
    tui.cursor_position = None
    return tui


class _FullWidthRows(Component):
    """Fills the width it is given, then optionally lets a SIGWINCH land.

    ``fill`` differs per frame so the diff engine actually has rows to repaint
    — an unchanged frame short-circuits before writing anything.
    """

    def __init__(self, fill: str = "x", on_render=None) -> None:
        self._fill = fill
        self._on_render = on_render

    def render(self, width: int) -> list[str]:
        rows = [self._fill * width for _ in range(3)]
        if self._on_render is not None:
            self._on_render()
        return rows


def _painted_row_widths(term: _CaptureTerminal) -> list[int]:
    painted = "".join(term.out)
    rows = strip_ansi(painted).split("\r\n")
    return [visible_width(r.lstrip("\r")) for r in rows if r]


def test_sigwinch_does_not_publish_the_size_mid_frame(monkeypatch) -> None:
    """Rows must never be wider than the terminal the engine painted them into.

    The engine commits the width it used as ``_prev_width``, so that is the
    width the frame is claimed to fit. Publishing a shrink between the layout
    read and the diff read breaks the claim: 119-column rows go out while the
    engine records a 60-column terminal, every row soft-wraps onto a second
    physical row, and the committed width means no later frame detects it.
    """
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 10)))
    term = _CaptureTerminal()
    renderer = StringRenderer(term)

    def _shrink() -> None:
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (60, 10)))
        term._on_resize()  # the signal lands mid-render

    async def scenario() -> list[int]:
        renderer.render(_tui_with([_FullWidthRows("x")]))  # steady state at 120
        term.out.clear()
        renderer.render(_tui_with([_FullWidthRows("y", _shrink)]))
        return _painted_row_widths(term)

    widths = asyncio.run(scenario())
    committed = renderer._engine._prev_width

    assert widths, "expected the frame to paint something"
    assert max(widths) <= committed, (
        f"painted rows up to {max(widths)} columns into a terminal the engine "
        f"committed as {committed} columns wide"
    )


def test_size_and_callbacks_land_together_on_the_loop(monkeypatch) -> None:
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 10)))
    term = _CaptureTerminal()
    seen: list[tuple[int, int]] = []
    term.on_resize(lambda: seen.append((term.width, term.height)))

    async def scenario() -> None:
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (60, 20)))
        term._on_resize()
        # Still the old size: the handler only staged it.
        assert (term.width, term.height) == (120, 10)
        assert seen == []
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert (term.width, term.height) == (60, 20)
    # Subscribers must observe the new size, not the one being replaced.
    assert seen == [(60, 20)]


def test_size_is_published_inline_without_a_running_loop(monkeypatch) -> None:
    """Startup has no loop yet; deferring there would drop the update."""
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 10)))
    term = _CaptureTerminal()
    fired: list[None] = []
    term.on_resize(lambda: fired.append(None))

    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (60, 20)))
    term._on_resize()

    assert (term.width, term.height) == (60, 20)
    assert fired == [None]


def test_a_resize_between_frames_still_repaints_at_the_new_width(monkeypatch) -> None:
    """The deferral must not cost the corrective full redraw."""
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (120, 10)))
    term = _CaptureTerminal()
    renderer = StringRenderer(term)

    async def scenario() -> list[int]:
        renderer.render(_tui_with([_FullWidthRows()]))
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (60, 10)))
        term._on_resize()
        await asyncio.sleep(0)  # loop adopts the new size + arms the redraw
        term.out.clear()
        renderer.render(_tui_with([_FullWidthRows()]))
        return _painted_row_widths(term)

    widths = asyncio.run(scenario())

    assert widths
    assert max(widths) <= 60


def test_a_direct_read_is_not_clobbered_by_a_queued_publish(monkeypatch) -> None:
    """enter_raw_mode re-reads the size; a stale staged one must not land after.

    Once the size is staged rather than assigned, a direct read can overtake a
    queued one — which is exactly what happens coming back from `suspended()`,
    where resizes during an external editor were never delivered. Letting the
    stale value land would leave every consumer sizing to a window that no
    longer exists, and nothing would detect it: the renderer's width comparison
    would agree with the wrong number.
    """
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (200, 50)))
    term = _CaptureTerminal()

    async def scenario() -> None:
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (80, 20)))
        term._on_resize()  # stages (80, 20)

        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (160, 40)))
        term._pending_size = None
        term.width, term.height = term._get_size()

        await asyncio.sleep(0)  # the queued publish runs here

    asyncio.run(scenario())

    assert (term.width, term.height) == (160, 40)


def test_the_latest_signal_still_wins(monkeypatch) -> None:
    """Dropping superseded values must not drop the current one."""
    monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (200, 50)))
    term = _CaptureTerminal()

    async def scenario() -> None:
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (90, 22)))
        term._on_resize()
        monkeypatch.setattr(Terminal, "_get_size", staticmethod(lambda: (70, 18)))
        term._on_resize()
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert (term.width, term.height) == (70, 18)
