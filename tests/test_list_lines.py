"""A List renders its visible window of items as lines.

These pin the invariants the old cell implementation gave for free — exactly
``height`` rows, nothing overrunning ``width`` columns, and a scroll offset
that settles deterministically — plus selection highlighting and the wide
glyph cases (CJK, ZWJ emoji) where per-character measurement goes wrong.
"""

from __future__ import annotations

import pytest

from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import strip_ansi, visible_width
from tau.tui.widgets.list import List, ListDirection, ListItem, ListState


def _items(*specs: str | Line) -> list[ListItem]:
    return [ListItem(Line.from_like(s) if not isinstance(s, Line) else s) for s in specs]


CASES = {
    "plain": _items("alpha", "beta", "gamma"),
    "single": _items("only"),
    "many": _items(*[f"item {i}" for i in range(20)]),
    "cjk": _items("日本語", "テキスト"),
    "emoji": _items("🎉 party", "👨\u200d👩\u200d👧 family"),
    "styled spans": [ListItem(Line([Span("st", Style().bold()), Span("yled")]))],
    "line style": [ListItem(Line([Span("x")], style=Style().dim())), ListItem(Line.raw("y"))],
    "tall": [
        ListItem([Line.raw("first"), Line.raw("  cont")]),
        ListItem(Line.raw("second")),
    ],
}


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("width", [6, 14, 30])
@pytest.mark.parametrize("height", [1, 3, 8])
@pytest.mark.parametrize("direction", [ListDirection.TOP_TO_BOTTOM, ListDirection.BOTTOM_TO_TOP])
def test_rows_fill_the_box_without_overrunning(
    name: str, width: int, height: int, direction: ListDirection
) -> None:
    """Exactly ``height`` rows, and no row wider than ``width`` columns."""
    items = CASES[name]
    for selected in (None, 0, min(1, len(items) - 1), len(items) - 1):
        lst = List(items=list(items), direction=direction)
        rows = lst.render_lines(width, height, ListState(selected=selected))
        assert len(rows) == height, f"{name} sel={selected}"
        for row in rows:
            assert visible_width(row) <= width, f"{name} sel={selected}: {row!r}"


@pytest.mark.parametrize("name", list(CASES))
def test_scroll_offset_is_deterministic(name: str) -> None:
    """Two identical renders settle on the same offset."""
    items = CASES[name]
    for selected in (None, 0, len(items) - 1):
        a, b = List(items=list(items)), List(items=list(items))
        sa, sb = ListState(selected=selected), ListState(selected=selected)
        a.render_lines(14, 3, sa)
        b.render_lines(14, 3, sb)
        assert sa.offset == sb.offset


def test_selection_bar_spans_the_full_width() -> None:
    """The highlight covers trailing blanks, not just the text."""
    lst = List(items=_items("short", "other"), highlight_style=Style().reversed())
    rows = lst.render_lines(20, 3, ListState(selected=0))
    assert visible_width(rows[0]) == 20


def test_a_wide_glyph_item_is_not_split_across_the_edge() -> None:
    """日本語 is six columns; clipping must land on a glyph boundary."""
    lst = List(items=_items("日本語テキスト"))
    row = strip_ansi(lst.render_lines(8, 1, ListState())[0])
    assert visible_width(row) <= 8
    assert "\ufffd" not in row


def test_unused_rows_are_blank() -> None:
    lst = List(items=_items("a"))
    rows = lst.render_lines(10, 4, ListState(selected=0))
    assert len(rows) == 4
    assert rows[1:] == ["", "", ""]


def test_degenerate_sizes() -> None:
    lst = List(items=_items("a"))
    assert lst.render_lines(0, 3, ListState()) == ["", "", ""]
    assert lst.render_lines(10, 0, ListState()) == []
    assert List(items=[]).render_lines(10, 2, ListState()) == ["", ""]
