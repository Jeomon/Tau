"""Multi-row ``ListItem`` support in tau/tui/widgets/list.py.

Mirrors Ratatui, whose ``ListItem`` wraps a ``Text`` and reports a ``height``.
The single-row path must stay byte-identical — every selector in the app
depends on it — so these tests check both.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line
from tau.tui.widgets.list import List, ListDirection, ListItem, ListState


def _line(text: str) -> Line:
    return Line.raw(text)


def _render(items, height=10, width=30, state=None, **kwargs) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, width, height))
    List(items=items, **kwargs).render(Rect(0, 0, width, height), buf, state or ListState())
    return [
        "".join(buf.get(x, y).symbol for x in range(width)).rstrip() for y in range(height)
    ]


class TestHeight:
    def test_single_line_item_is_one_row(self):
        assert ListItem(_line("a")).height == 1
        assert ListItem(_line("a")).lines == [_line("a")]

    def test_multi_line_item_reports_its_rows(self):
        item = ListItem([_line("a"), _line("b"), _line("c")])
        assert item.height == 3
        assert len(item.lines) == 3

    def test_empty_line_list_still_occupies_a_row(self):
        assert ListItem([]).height == 1


class TestTallRendering:
    def test_every_row_of_a_tall_item_is_drawn(self):
        rows = _render([ListItem([_line("title"), _line("  description")]), ListItem(_line("x"))])

        assert rows[0].endswith("title")
        assert rows[1].endswith("description")
        assert rows[2].endswith("x")

    def test_following_items_start_below_the_tall_one(self):
        rows = _render(
            [
                ListItem([_line("one"), _line("one-cont"), _line("one-more")]),
                ListItem(_line("two")),
            ]
        )

        assert "two" in rows[3]
        assert rows[4] == ""

    def test_the_cursor_symbol_marks_only_the_first_row(self):
        state = ListState(selected=0)
        rows = _render(
            [ListItem([_line("title"), _line("description")])],
            state=state,
            highlight_symbol="> ",
        )

        assert rows[0].startswith("> ")
        assert rows[1].startswith("  ")  # continuation is indented, not re-marked

    def test_an_item_taller_than_the_viewport_is_clipped_not_dropped(self):
        rows = _render([ListItem([_line(f"r{i}") for i in range(6)])], height=3)

        assert [r.strip() for r in rows] == ["r0", "r1", "r2"]

    def test_scrolls_so_the_selected_item_fits(self):
        items = [ListItem([_line(f"item{i}"), _line(f"  desc{i}")]) for i in range(5)]
        state = ListState(selected=4, offset=0)

        rows = _render(items, height=4, state=state)

        # Two items fit in four rows; the offset advanced to reach item 4.
        assert state.offset == 3
        assert any("item4" in r for r in rows)
        assert not any("item0" in r for r in rows)

    def test_bottom_anchored_direction_hugs_the_bottom(self):
        rows = _render(
            [ListItem([_line("a"), _line("b")])],
            height=5,
            direction=ListDirection.BOTTOM_TO_TOP,
        )

        assert rows[0] == "" and rows[1] == "" and rows[2] == ""
        assert rows[3].endswith("a")
        assert rows[4].endswith("b")


class TestSingleRowPathUnchanged:
    def test_uniform_lists_still_render_one_per_row(self):
        rows = _render([ListItem(_line(f"item{i}")) for i in range(3)])

        assert [r.strip() for r in rows[:3]] == ["item0", "item1", "item2"]

    def test_selection_highlight_still_applies(self):
        state = ListState(selected=1)
        rows = _render(
            [ListItem(_line("a")), ListItem(_line("b"))], state=state, highlight_symbol="> "
        )

        assert rows[1].startswith("> ")

    def test_offset_still_scrolls(self):
        state = ListState(selected=None, offset=2)
        rows = _render([ListItem(_line(f"i{n}")) for n in range(6)], height=2, state=state)

        assert [r.strip() for r in rows] == ["i2", "i3"]

    def test_style_is_applied_per_item(self):
        rows = _render([ListItem(_line("styled"), Style().bold())])
        assert rows[0].endswith("styled")
