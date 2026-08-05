"""End-to-end screen output for StringRenderer, driven through a real TUI.

These were differential tests, comparing the string pipeline against the cell
one frame by frame. That oracle is gone with the cell renderer, so they now
assert the resulting screen directly — weaker in principle, but the values
were captured while both pipelines were still present and agreeing.

Covers the pieces the renderer is responsible for stitching together: plain
and styled children, wrapping, MessageList's frozen/live split, overlays at
several positions, cursor propagation through nesting, successive frames (the
diff path, not just first paint), and a resize.
"""

from __future__ import annotations

import pytest

from tau.message.types import TextContent, UserMessage
from tau.modes.interactive.components.message_list import MessageBlock, MessageList
from tau.tui.component import Component
from tau.tui.geometry import Position, Rect
from tau.tui.service import StringRenderer
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


def _screen(children, overlays=None, width=60, height=12):
    """Render one frame and return the resulting screen rows (SGR stripped)."""
    from tau.tui.utils import strip_ansi

    term = _Term(width, height)
    StringRenderer(term).render(_tui_with(children), overlays)  # type: ignore[arg-type]
    return [strip_ansi(r).rstrip() for r in term.screen.snapshot()]


def test_plain_children() -> None:
    assert _screen([Lines(["alpha", "beta"]), Lines(["gamma"])]) == [
        " alpha",
        " beta",
        " gamma",
    ]


def test_styled_children_keep_their_text() -> None:
    assert _screen([Lines(["\x1b[31mred\x1b[0m", "plain"])]) == [" red", " plain"]


def test_long_lines_wrap_to_the_content_width() -> None:
    rows = _screen([Lines(["word " * 40])])
    assert len(rows) > 1
    assert all(len(r) <= 60 for r in rows)
    # every word survives the wrap, none duplicated or dropped
    assert " ".join(rows).split() == ["word"] * 40


def test_message_list_child_matches() -> None:
    ml = MessageList()
    for i in range(4):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"message {i}")]))
        blk.finalize()
        ml.add_block(blk)
    rows = _screen([ml])
    assert [r.strip() for r in rows if r.strip()] == [
        "❯ message 0",
        "❯ message 1",
        "❯ message 2",
        "❯ message 3",
    ]


def test_message_list_plus_siblings_matches() -> None:
    ml = MessageList()
    for i in range(3):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"m{i}")]))
        blk.finalize()
        ml.add_block(blk)
    rows = [
        r.strip() for r in _screen([Lines(["== header =="]), ml, Lines(["> prompt"])]) if r.strip()
    ]
    assert rows[0] == "== header =="
    assert rows[-1] == "> prompt"
    assert "❯ m0" in rows


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
    rows = _screen([Lines([f"base line {i}" for i in range(8)])], [overlay])
    painted = "\n".join(rows)
    assert "menu" in painted
    # the overlay must not smear across every row, nor drop base content
    assert sum("menu" in r for r in rows) == 1
    assert any("base line" in r for r in rows)


def test_overlay_over_a_message_list_matches() -> None:
    ml = MessageList()
    for i in range(5):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"line {i}")]))
        blk.finalize()
        ml.add_block(blk)
    overlay = _Overlay(Lines(["[ picker ]"]), 10, 1, 6)
    rows = _screen([ml], [overlay])
    assert sum("[ picker ]" in r for r in rows) == 1
    assert any("line 0" in r for r in rows)


def test_successive_frames_land_on_the_right_screen() -> None:
    """The diff path, not just first paint: rows are appended one per frame."""
    from tau.tui.utils import strip_ansi

    term = _Term()
    r = StringRenderer(term)  # type: ignore[arg-type]
    for n in range(1, 7):
        r.render(_tui_with([Lines([f"row {i}" for i in range(n)])]))
    rows = [strip_ansi(x).rstrip() for x in term.screen.snapshot()]
    assert [x.strip() for x in rows if x.strip()] == [f"row {i}" for i in range(6)]


def test_resize_repaints_the_content() -> None:
    from tau.tui.utils import strip_ansi

    term = _Term()
    r = StringRenderer(term)  # type: ignore[arg-type]
    children = [Lines([f"content row {i}" for i in range(5)])]
    r.render(_tui_with(children))
    term.width = 44
    term.fire_resize()
    r.render(_tui_with(children))
    rows = [strip_ansi(x).strip() for x in term.screen.snapshot()]
    assert [x for x in rows if x] == [f"content row {i}" for i in range(5)]
