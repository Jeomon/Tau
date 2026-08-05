"""Block.render_lines must paint what the cell-writing form painted.

A Block draws its own frame and the caller places content into ``inner(...)``.
That frame is now produced as lines; ``render(area, buf)`` is implemented in
terms of it so there is one definition rather than two that can drift.

The reference below is a verbatim copy of the pre-pivot implementation.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_into
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.widgets.block import Block, Borders, TitlePosition


def _render_original(self: Block, area: Rect, buf: Buffer) -> None:
    if area.is_empty():
        return
    if self.style != Style():
        buf.set_style(area, self.style)
    b, s = self.border_set, self.border_style
    has_top, has_bottom = Borders.TOP in self.borders, Borders.BOTTOM in self.borders
    has_left, has_right = Borders.LEFT in self.borders, Borders.RIGHT in self.borders
    if has_top:
        buf.set_string(area.left, area.top, b.horizontal * area.width, s)
    if has_bottom and area.height > 1:
        buf.set_string(area.left, area.bottom - 1, b.horizontal * area.width, s)
    if has_left:
        for y in range(area.top, area.bottom):
            buf.set(area.left, y, b.vertical, s)
    if has_right and area.width > 1:
        for y in range(area.top, area.bottom):
            buf.set(area.right - 1, y, b.vertical, s)
    if has_top and has_left:
        buf.set(area.left, area.top, b.top_left, s)
    if has_top and has_right and area.width > 1:
        buf.set(area.right - 1, area.top, b.top_right, s)
    if has_bottom and has_left and area.height > 1:
        buf.set(area.left, area.bottom - 1, b.bottom_left, s)
    if has_bottom and has_right and area.height > 1 and area.width > 1:
        buf.set(area.right - 1, area.bottom - 1, b.bottom_right, s)
    for title in self.titles:
        on_top = title.position is TitlePosition.TOP
        row = area.top if on_top else area.bottom - 1
        if row < area.top or row >= area.bottom:
            continue
        left_inset = 2 if has_left else 1
        right_inset = 1 if has_right else 0
        x = area.left + left_inset
        width = max(0, (area.right - right_inset) - x)
        line = Line(list(title.content.spans), title.content.style, title.alignment)
        buf.set_line(x, row, line, width)


def _lines_to_cells(lines: list[str], width: int, height: int) -> Buffer:
    buf = Buffer.empty(Rect(0, 0, width, height))
    for i, line in enumerate(lines):
        parse_ansi_into(buf, 0, i, line, width)
    return buf


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
def test_lines_match_the_cell_implementation(name: str, width: int, height: int) -> None:
    block = CASES[name]
    expected = Buffer.empty(Rect(0, 0, width, height))
    _render_original(block, Rect(0, 0, width, height), expected)
    got = _lines_to_cells(block.render_lines(width, height), width, height)
    assert got.content == expected.content


def test_a_title_does_not_blank_the_border_around_it() -> None:
    """The title writes only its own columns; the rule either side survives."""
    from tau.tui.utils import strip_ansi

    block = Block(borders=Borders.ALL).with_title(Line([Span(" T ")]))
    top = strip_ansi(block.render_lines(20, 3)[0])
    assert top.startswith("┌─ T ─")
    assert top.endswith("┐")


def test_buffer_form_agrees_with_the_line_form() -> None:
    block = CASES["titled"]
    buf = Buffer.empty(Rect(0, 0, 20, 3))
    block.render(Rect(0, 0, 20, 3), buf)
    assert buf.content == _lines_to_cells(block.render_lines(20, 3), 20, 3).content


def test_degenerate_sizes() -> None:
    assert Block(borders=Borders.ALL).render_lines(0, 3) == []
    assert Block(borders=Borders.ALL).render_lines(10, 0) == []


def test_dynamic_border_is_a_full_width_rule() -> None:
    from tau.tui.components.box import DynamicBorder
    from tau.tui.utils import strip_ansi, visible_width

    out = DynamicBorder().render(20)
    assert len(out) == 1
    assert visible_width(out[0]) == 20
    assert set(strip_ansi(out[0])) == {"─"}
