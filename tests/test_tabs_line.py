"""Tabs is a single row of styled runs, so it produces a line.

These pin what the old cell-writing form gave for free: the strip never
overruns ``width`` columns (it clips at the right edge rather than wrapping
onto a second row), padding and dividers land where they should, and styles
survive. Wide glyphs are the interesting case — a CJK or emoji title is two
columns per glyph, so per-character measurement overruns.
"""

from __future__ import annotations

import pytest

from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import strip_ansi, visible_width
from tau.tui.widgets.tabs import Tabs

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
def test_strip_never_overruns_its_width(name: str, pad_l: int, pad_r: int, width: int) -> None:
    """Clips at the right edge — a strip wider than the terminal must not wrap."""
    titles = TITLE_SETS[name]
    for selected in range(len(titles)):
        tabs = Tabs(titles, selected=selected, padding_left=pad_l, padding_right=pad_r)
        out = tabs.render_line(width)
        assert visible_width(out) <= width, f"{name} pad={pad_l},{pad_r} sel={selected}: {out!r}"
        assert "\n" not in out


def test_titles_appear_in_order_separated_by_the_divider() -> None:
    tabs = Tabs(["one", "two", "three"], selected=0)
    out = strip_ansi(tabs.render_line(40))
    assert out.index("one") < out.index("two") < out.index("three")
    assert tabs.divider.strip() in out


def test_padding_surrounds_each_title() -> None:
    out = strip_ansi(Tabs(["a", "b"], selected=0, padding_left=2, padding_right=2).render_line(30))
    assert "  a  " in out


def test_a_wide_glyph_title_is_measured_in_columns() -> None:
    """日本語です is ten columns, so it alone fills a ten-column strip."""
    out = Tabs(["日本語です", "x"], selected=0).render_line(10)
    assert visible_width(out) <= 10
    assert "\ufffd" not in strip_ansi(out)


def test_line_level_style_is_not_dropped() -> None:
    """The line's own style merges behind each span."""
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
