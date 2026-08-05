"""StringRenderer must put the same thing on screen as Renderer, via a real TUI.

This is the end-to-end check for the swap: same component tree, same overlays,
same focused cursor — one frame driven through the cell pipeline
(Renderer -> ScrollbackTerminal) and one through the string pipeline
(StringRenderer -> ScrollbackRenderer), compared on resulting screen state.
"""

from __future__ import annotations

import pytest

from tau.message.types import TextContent, UserMessage
from tau.modes.interactive.components.message_list import MessageBlock, MessageList
from tau.tui.component import Component
from tau.tui.geometry import Position, Rect
from tau.tui.service import Renderer, StringRenderer
from tests.test_scrollback_renderer import Screen


class _Term:
    def __init__(self, width: int = 60, height: int = 12) -> None:
        self.width, self.height = width, height
        self.screen = Screen(width)
        self._cbs: list = []

    def write(self, s: str) -> None:
        self.screen.feed(s)

    write_flush = write

    def flush(self) -> None:
        pass

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def on_resize(self, cb):
        self._cbs.append(cb)
        return lambda: None

    def fire_resize(self) -> None:
        for cb in list(self._cbs):
            cb()


class Lines(Component):
    """A plain cell-based component, i.e. one that has not been migrated."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render_cells(self, area: Rect, buf) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        row = 0
        for line in self._lines:
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class WithCursor(Lines):
    """A focused, cursor-bearing component (what TextInput is)."""

    def render_cells(self, area: Rect, buf) -> int:
        rows = super().render_cells(area, buf)
        buf.cursor_position = Position(area.x + 4, area.y)
        return rows


class _Overlay:
    """Minimal stand-in for an overlay stack entry."""

    def __init__(self, component: Component, width: int, row: int, col: int) -> None:
        self.component = component
        self._w, self._row, self._col = width, row, col

    def is_visible(self, width: int, height: int) -> bool:
        return True

    def resolve_width(self, width: int) -> int:
        return self._w

    def resolve(self, width: int, height: int, natural_h: int):
        return self._w, natural_h, self._row, self._col


def _tui_with(children: list[Component]):
    from tau.tui.service import TUI

    # Bypass __init__: it builds a terminal, event loop wiring and input
    # parser this test has no use for. Only the render-path state is needed,
    # including the cell path's caches so both pipelines start equal.
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


def _screens(children, overlays=None, width=60, height=12):
    ta, tb = _Term(width, height), _Term(width, height)
    cell = Renderer(ta)  # type: ignore[arg-type]
    string = StringRenderer(tb)  # type: ignore[arg-type]
    cell.render(_tui_with(children), overlays)
    string.render(_tui_with(children), overlays)
    return ta.screen.snapshot(), tb.screen.snapshot()


def test_plain_children_match() -> None:
    a, b = _screens([Lines(["alpha", "beta"]), Lines(["gamma"])])
    assert a == b


def test_styled_children_match() -> None:
    a, b = _screens([Lines(["\x1b[31mred\x1b[0m", "plain"])])
    assert a == b


def test_wrapping_children_match() -> None:
    a, b = _screens([Lines(["word " * 40])])
    assert a == b


def test_message_list_child_matches() -> None:
    ml = MessageList()
    for i in range(4):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"message {i}")]))
        blk.finalize()
        ml.add_block(blk)
    a, b = _screens([ml])
    assert a == b


def test_message_list_plus_siblings_matches() -> None:
    ml = MessageList()
    for i in range(3):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"m{i}")]))
        blk.finalize()
        ml.add_block(blk)
    a, b = _screens([Lines(["== header =="]), ml, Lines(["> prompt"])])
    assert a == b


def test_cursor_position_survives_the_bridge() -> None:
    """A focused component's cursor must reach the engine through the string path."""
    tb = _Term()
    string = StringRenderer(tb)  # type: ignore[arg-type]
    tui = _tui_with([Lines(["header"]), WithCursor(["type here"])])
    string.render(tui)
    # row 1 (after the header), column 4 plus the renderer's left pad
    assert tui.cursor_position == Position(4, 1)


@pytest.mark.parametrize(
    ("row", "col", "ov_w"), [(0, 0, 8), (2, 5, 10), (1, 40, 12)], ids=["origin", "mid", "right"]
)
def test_overlays_match(row: int, col: int, ov_w: int) -> None:
    overlay = _Overlay(Lines(["+------+", "| menu |", "+------+"]), ov_w, row, col)
    a, b = _screens([Lines([f"base line {i}" for i in range(8)])], [overlay])
    assert a == b


def test_overlay_over_a_message_list_matches() -> None:
    ml = MessageList()
    for i in range(5):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"line {i}")]))
        blk.finalize()
        ml.add_block(blk)
    overlay = _Overlay(Lines(["[ picker ]"]), 10, 1, 6)
    a, b = _screens([ml], [overlay])
    assert a == b


def test_successive_frames_match() -> None:
    """Diff paths, not just first paint."""
    ta, tb = _Term(), _Term()
    cell, string = Renderer(ta), StringRenderer(tb)  # type: ignore[arg-type]
    for n in range(1, 7):
        children = [Lines([f"row {i}" for i in range(n)])]
        cell.render(_tui_with(children))
        string.render(_tui_with(children))
    assert ta.screen.snapshot() == tb.screen.snapshot()


def test_frames_match_across_a_resize() -> None:
    ta, tb = _Term(), _Term()
    cell, string = Renderer(ta), StringRenderer(tb)  # type: ignore[arg-type]
    children = [Lines([f"content row {i}" for i in range(5)])]
    cell.render(_tui_with(children))
    string.render(_tui_with(children))
    ta.width = tb.width = 44
    ta.fire_resize()
    tb.fire_resize()
    cell.render(_tui_with(children))
    string.render(_tui_with(children))
    assert ta.screen.snapshot() == tb.screen.snapshot()
