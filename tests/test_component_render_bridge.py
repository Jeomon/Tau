"""A Component implements exactly one render contract; the base bridges the other.

This is the migration scaffolding: it lets components move from
``render_cells(area, buf)`` to ``render(width) -> list[str]`` one at a time
with the suite green, rather than requiring one flag day. These tests pin that
either implementation works from either caller, and that mixing the two inside
one tree produces the same result as not mixing them.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_wrapped_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.component import Component, Container
from tau.tui.geometry import Rect

WIDTH = 30


class CellStyle(Component):
    """Legacy contract: writes cells."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        row = 0
        for line in self._lines:
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class StringStyle(Component):
    """Target contract: returns lines."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render(self, width: int) -> list[str]:
        return list(self._lines)


class Neither(Component):
    """Implements neither — must fail loudly, not recurse forever."""


def _cells_to_lines(component: Component, width: int = WIDTH) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    rows = component.render_cells(Rect(0, 0, width, 0), buf)
    return [row_to_ansi(buf, y, embed_raw=True).rstrip() for y in range(rows)]


LINES = ["first line", "\x1b[31msecond\x1b[0m line", "third"]


def test_cell_component_can_be_rendered_as_strings() -> None:
    got = [ln.rstrip() for ln in CellStyle(LINES).render(WIDTH)]
    assert got == LINES


def test_string_component_can_be_rendered_as_cells() -> None:
    assert _cells_to_lines(StringStyle(LINES)) == LINES


def test_both_contracts_agree_through_either_caller() -> None:
    cell, string = CellStyle(LINES), StringStyle(LINES)
    assert [x.rstrip() for x in cell.render(WIDTH)] == [x.rstrip() for x in string.render(WIDTH)]
    assert _cells_to_lines(cell) == _cells_to_lines(string)


def test_container_mixing_both_kinds_of_child() -> None:
    """A half-migrated tree must render exactly like an unmigrated one."""
    mixed = Container()
    mixed.children = [CellStyle(["a"]), StringStyle(["b"]), CellStyle(["c"])]

    all_cells = Container()
    all_cells.children = [CellStyle(["a"]), CellStyle(["b"]), CellStyle(["c"])]

    all_strings = Container()
    all_strings.children = [StringStyle(["a"]), StringStyle(["b"]), StringStyle(["c"])]

    via_strings = [[x.rstrip() for x in c.render(WIDTH)] for c in (mixed, all_cells, all_strings)]
    assert via_strings[0] == via_strings[1] == via_strings[2] == ["a", "b", "c"]

    via_cells = [_cells_to_lines(c) for c in (mixed, all_cells, all_strings)]
    assert via_cells[0] == via_cells[1] == via_cells[2] == ["a", "b", "c"]


def test_container_keeps_a_migrated_child_on_strings() -> None:
    """The container must not round-trip a string child through cells."""
    seen: list[int] = []

    class Tracked(StringStyle):
        def render_cells(self, area: Rect, buf: Buffer) -> int:
            seen.append(1)
            return super().render_cells(area, buf)

    parent = Container()
    parent.children = [Tracked(["x"])]
    assert [ln.rstrip() for ln in parent.render(WIDTH)] == ["x"]
    assert seen == []  # never bridged back through cells


def test_wrapping_is_preserved_across_the_bridge() -> None:
    long_line = "word " * 20
    cell, string = CellStyle([long_line]), StringStyle([long_line])
    assert len(_cells_to_lines(cell)) == len(_cells_to_lines(string)) > 1


def test_component_implementing_neither_raises_instead_of_recursing() -> None:
    with pytest.raises(TypeError, match="implements neither"):
        Neither().render(WIDTH)
    with pytest.raises(TypeError, match="implements neither"):
        Neither().render_cells(Rect(0, 0, WIDTH, 0), Buffer.empty(Rect(0, 0, WIDTH, 0)))


def test_empty_component_renders_nothing() -> None:
    assert StringStyle([]).render(WIDTH) == []
    assert _cells_to_lines(CellStyle([])) == []
