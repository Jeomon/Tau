"""Tabs.render_line must paint what the cell-writing form painted.

Tabs is a single row of styled runs, so it produces a line now. The buffer
form is kept for callers still holding one, and is implemented in terms of the
line — these tests pin the line against the *original* cell-writing logic,
reproduced below, so the pivot to lines cannot have changed what lands on
screen.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_into
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.widgets.tabs import Tabs


def _render_original(t: Tabs, area: Rect, buf: Buffer) -> None:
    """Verbatim copy of the pre-pivot cell-writing implementation."""
    if area.is_empty() or not t.titles:
        return
    x, end = area.left, area.right
    for i, title in enumerate(t.titles):
        if x >= end:
            break
        style = t.highlight_style if i == t.selected else t.style
        box_width = min(t.padding_left + title.width + t.padding_right, end - x)
        if t.padding_left:
            buf.set_string(x, area.top, " " * t.padding_left, style, box_width)
        title_x = x + min(t.padding_left, box_width)
        title_width = max(0, box_width - t.padding_left - t.padding_right)
        buf.set_line(title_x, area.top, title.patch_style(style), title_width)
        if t.padding_right:
            pad_x = title_x + title_width
            remaining = max(0, x + box_width - pad_x)
            buf.set_string(pad_x, area.top, " " * t.padding_right, style, remaining)
        x += box_width
        if i < len(t.titles) - 1 and x < end:
            x = buf.set_string(x, area.top, t.divider, t.style, end - x)


def _cells_of_line(text: str, width: int):
    buf = Buffer.empty(Rect(0, 0, width, 1))
    parse_ansi_into(buf, 0, 0, text, width)
    return buf.content


TITLE_SETS = {
    "plain": ["one", "two", "three"],
    "single": ["a"],
    "spaces": ["tab 1", "tab 2"],
    "cjk": ["日本", "語"],
    "long cjk": ["日本語です", "x"],
    "emoji": ["🎉 x", "y"],
    "styled spans": [Line([Span("st", Style().bold()), Span("yled")]), "plain"],
    "line-level style": [Line([Span("x")], style=Style().dim()), "y"],
}


@pytest.mark.parametrize("name", list(TITLE_SETS))
@pytest.mark.parametrize(("pad_l", "pad_r"), [(0, 0), (1, 1), (2, 0), (0, 2), (2, 2)])
@pytest.mark.parametrize("width", [3, 5, 8, 12, 20, 40])
def test_line_matches_the_cell_implementation(
    name: str, pad_l: int, pad_r: int, width: int
) -> None:
    titles = TITLE_SETS[name]
    for selected in range(len(titles)):
        line_tabs = Tabs(titles, selected=selected, padding_left=pad_l, padding_right=pad_r)
        cell_tabs = Tabs(titles, selected=selected, padding_left=pad_l, padding_right=pad_r)
        expected = Buffer.empty(Rect(0, 0, width, 1))
        _render_original(cell_tabs, Rect(0, 0, width, 1), expected)
        assert _cells_of_line(line_tabs.render_line(width), width) == expected.content


def test_line_level_style_is_not_dropped() -> None:
    """set_line merges line.style behind each span; the line form must too."""
    tabs = Tabs([Line([Span("x")], style=Style().dim())], selected=99)
    assert "\x1b[" in tabs.render_line(10)


def test_selected_tab_is_highlighted() -> None:
    tabs = Tabs(["one", "two"], selected=1, highlight_style=Style().bold())
    out = tabs.render_line(20)
    assert out.index("two") > out.index("one")
    assert "\x1b[1m" in out


def test_empty_cases() -> None:
    assert Tabs([]).render_line(20) == ""
    assert Tabs(["a"]).render_line(0) == ""


def test_buffer_form_still_works_for_widget_callers() -> None:
    """WidgetComponent and extensions still hand it a Buffer."""
    tabs = Tabs(["one", "two"], selected=0)
    buf = Buffer.empty(Rect(0, 0, 20, 1))
    tabs.render(Rect(0, 0, 20, 1), buf)
    assert buf.content == _cells_of_line(tabs.render_line(20), 20)
