"""A Block draws its own frame and the caller places content into ``inner(...)``.

The frame is produced as lines. These pin the invariants the old cell
implementation gave for free — exactly ``height`` rows, each exactly ``width``
*columns* wide — plus the frame and title placement itself. Column width is
the interesting part: a CJK title occupies two columns per glyph, so a naive
per-character implementation overruns the right border.
"""

from __future__ import annotations

import pytest

from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import strip_ansi, visible_width
from tau.tui.widgets.block import Block, Borders

CASES = {
    "all borders": Block(borders=Borders.ALL),
    "top only": Block(borders=Borders.TOP),
    "bottom only": Block(borders=Borders.BOTTOM),
    "no borders": Block(borders=Borders.NONE),
    "left and right": Block(borders=Borders.LEFT | Borders.RIGHT),
    "styled border": Block(borders=Borders.ALL, border_style=Style().dim()),
    "titled": Block(borders=Borders.ALL).with_title(Line([Span(" Preview ")])),
    "styled title": Block(borders=Borders.ALL).with_title(Line([Span(" P ", Style().bold())])),
    "centred title": Block(borders=Borders.ALL).with_title(
        Line([Span(" C ")], alignment=Alignment.CENTER)
    ),
    "right title": Block(borders=Borders.ALL).with_title(
        Line([Span(" R ")], alignment=Alignment.RIGHT)
    ),
    "cjk title": Block(borders=Borders.ALL).with_title(Line([Span(" 日本 ")])),
}


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("width", [4, 10, 20, 40])
@pytest.mark.parametrize("height", [1, 2, 3, 6])
def test_frame_fills_exactly_the_requested_box(name: str, width: int, height: int) -> None:
    """Every row is exactly ``width`` columns — never over, never short."""
    rows = CASES[name].render_lines(width, height)
    assert len(rows) == height
    assert [visible_width(r) for r in rows] == [width] * height


@pytest.mark.parametrize("width", [10, 20, 40])
def test_all_borders_draws_the_expected_frame(width: int) -> None:
    rows = [strip_ansi(r) for r in Block(borders=Borders.ALL).render_lines(width, 3)]
    inner = width - 2
    assert rows[0] == "┌" + "─" * inner + "┐"
    assert rows[1] == "│" + " " * inner + "│"
    assert rows[2] == "└" + "─" * inner + "┘"


def test_a_title_does_not_blank_the_border_around_it() -> None:
    """The title writes only its own columns; the rule either side survives."""
    block = Block(borders=Borders.ALL).with_title(Line([Span(" T ")]))
    top = strip_ansi(block.render_lines(20, 3)[0])
    assert top.startswith("┌─ T ─")
    assert top.endswith("┐")


def test_a_wide_glyph_title_does_not_overrun_the_right_border() -> None:
    """日本 is four columns, not two — the frame must still close at ``width``."""
    top = strip_ansi(
        Block(borders=Borders.ALL).with_title(Line([Span(" 日本 ")])).render_lines(10, 3)[0]
    )
    assert top == "┌─ 日本 ─┐"
    assert visible_width(top) == 10


def test_partial_borders_only_draw_their_own_edges() -> None:
    top = [strip_ansi(r) for r in Block(borders=Borders.TOP).render_lines(10, 3)]
    assert top[0] == "─" * 10
    assert top[1] == " " * 10

    sides = [strip_ansi(r) for r in Block(borders=Borders.LEFT | Borders.RIGHT).render_lines(6, 2)]
    assert sides == ["│    │", "│    │"]


def test_no_borders_is_a_blank_box() -> None:
    assert [strip_ansi(r) for r in Block(borders=Borders.NONE).render_lines(5, 2)] == [
        "     ",
        "     ",
    ]


def test_degenerate_sizes() -> None:
    assert Block(borders=Borders.ALL).render_lines(0, 3) == []
    assert Block(borders=Borders.ALL).render_lines(10, 0) == []


def test_dynamic_border_is_a_full_width_rule() -> None:
    from tau.tui.components.box import DynamicBorder

    out = DynamicBorder().render(20)
    assert len(out) == 1
    assert visible_width(out[0]) == 20
    assert set(strip_ansi(out[0])) == {"─"}
