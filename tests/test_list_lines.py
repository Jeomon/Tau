"""List.render_lines must paint what the cell-writing form painted.

List is what every selector in the app renders through — /model, /theme,
/resume, /settings, the command palette, the file picker — so this is the
widget where a difference would be most visible and least excusable.

The reference below is a verbatim copy of the pre-pivot implementation,
including the whole-row ``set_style`` that draws the selection bar.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_into
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.widgets.list import List, ListDirection, ListItem, ListState


def _render_original(self: List, area: Rect, buf: Buffer, state: ListState) -> None:
    if area.is_empty() or not self.items:
        return
    if self.style != Style():
        buf.set_style(area, self.style)
    if any(item.height > 1 for item in self.items):
        _render_tall_original(self, area, buf, state)
        return
    state.ensure_visible(len(self.items), area.height)
    symbol_width = len(self.highlight_symbol)
    last = min(len(self.items), state.offset + area.height)
    visible_count = last - state.offset
    bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
    start_row = area.height - visible_count if bottom_anchored else 0
    for row, idx in enumerate(range(state.offset, last)):
        item = self.items[idx]
        y = area.top + start_row + row
        is_selected = idx == state.selected
        style = self.highlight_style.patch(item.style) if is_selected else item.style
        prefix = self.highlight_symbol if is_selected else " " * symbol_width
        buf.set_string(area.left, y, prefix, style)
        line = item.lines[0].patch_style(style)
        buf.set_line(area.left + symbol_width, y, line, max(0, area.width - symbol_width))
        if is_selected:
            buf.set_style(Rect(area.left, y, area.width, 1), self.highlight_style)


def _render_tall_original(self: List, area: Rect, buf: Buffer, state: ListState) -> None:
    symbol_width = len(self.highlight_symbol)
    heights = [item.height for item in self.items]
    offset = max(0, min(state.offset, len(self.items) - 1))
    selected = state.selected
    if selected is not None:
        offset = min(offset, selected)
        while offset < selected:
            if sum(heights[offset : selected + 1]) <= area.height:
                break
            offset += 1
    state.offset = offset
    rows_used = 0
    placed: list[tuple[int, int, int]] = []
    for idx in range(offset, len(self.items)):
        if rows_used >= area.height:
            break
        drawn = min(heights[idx], area.height - rows_used)
        placed.append((idx, rows_used, drawn))
        rows_used += drawn
    bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
    start_row = area.height - rows_used if bottom_anchored else 0
    for idx, top, drawn in placed:
        item = self.items[idx]
        is_selected = idx == state.selected
        style = self.highlight_style.patch(item.style) if is_selected else item.style
        for row, line in enumerate(item.lines[:drawn]):
            y = area.top + start_row + top + row
            prefix = self.highlight_symbol if (is_selected and row == 0) else " " * symbol_width
            buf.set_string(area.left, y, prefix, style)
            buf.set_line(
                area.left + symbol_width,
                y,
                line.patch_style(style),
                max(0, area.width - symbol_width),
            )
            if is_selected:
                buf.set_style(Rect(area.left, y, area.width, 1), self.highlight_style)


def _lines_to_cells(lines: list[str], width: int, height: int) -> Buffer:
    buf = Buffer.empty(Rect(0, 0, width, height))
    for i, line in enumerate(lines):
        if line:
            parse_ansi_into(buf, 0, i, line, width)
    return buf


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
def test_lines_match_the_cell_implementation(
    name: str, width: int, height: int, direction: ListDirection
) -> None:
    items = CASES[name]
    for selected in (None, 0, min(1, len(items) - 1), len(items) - 1):
        line_list = List(items=list(items), direction=direction)
        cell_list = List(items=list(items), direction=direction)
        line_state = ListState(selected=selected)
        cell_state = ListState(selected=selected)

        expected = Buffer.empty(Rect(0, 0, width, height))
        _render_original(cell_list, Rect(0, 0, width, height), expected, cell_state)
        got = _lines_to_cells(line_list.render_lines(width, height, line_state), width, height)
        assert got.content == expected.content, f"{name} sel={selected}"
        # the scroll offset the two paths settle on must agree too
        assert line_state.offset == cell_state.offset


def test_selection_bar_spans_the_full_width() -> None:
    """set_style patched the highlight over trailing blanks, not just the text."""
    from tau.tui.utils import visible_width

    lst = List(items=_items("short", "other"), highlight_style=Style().reversed())
    rows = lst.render_lines(20, 3, ListState(selected=0))
    assert visible_width(rows[0]) == 20


def test_unused_rows_are_blank() -> None:
    lst = List(items=_items("a"))
    rows = lst.render_lines(10, 4, ListState(selected=0))
    assert len(rows) == 4
    assert rows[1:] == ["", "", ""]


def test_buffer_form_agrees_with_the_line_form() -> None:
    items = _items("alpha", "beta")
    a, b = List(items=list(items)), List(items=list(items))
    buf = Buffer.empty(Rect(0, 0, 20, 3))
    a.render(Rect(0, 0, 20, 3), buf, ListState(selected=1))
    assert (
        buf.content == _lines_to_cells(b.render_lines(20, 3, ListState(selected=1)), 20, 3).content
    )


def test_degenerate_sizes() -> None:
    lst = List(items=_items("a"))
    assert lst.render_lines(0, 3, ListState()) == ["", "", ""]
    assert lst.render_lines(10, 0, ListState()) == []
    assert List(items=[]).render_lines(10, 2, ListState()) == ["", ""]
