"""Tests for SelectList's rendering onto List/ListState (Buffer-native).

Covers what the pre-existing test_tui_select_list.py (keyboard navigation)
doesn't: that scroll indicators/label-desc columns render correctly, and that
the selected row is visually distinguished.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.geometry import Rect
from tau.tui.style import Style


def _lines(select: SelectList, width: int) -> list[str]:
    from tau.tui.ansi_bridge import row_to_ansi

    buf = Buffer.empty(Rect(0, 0, width, 0))
    used = select.render_cells(Rect(0, 0, width, 0), buf)
    return [row_to_ansi(buf, y) for y in range(used)]


def test_scroll_indicators_shown_when_scrolled() -> None:
    select = SelectList([SelectItem(f"item{i}") for i in range(20)], max_visible=5)
    for _ in range(7):
        select.move_down()

    lines = _lines(select, 40)
    assert "↑ 3 more" in lines[0]
    assert "↓" in lines[-1] and "more" in lines[-1]
    assert len(lines) == 7  # 1 top indicator + 5 visible rows + 1 bottom indicator


def test_no_indicators_when_all_items_fit() -> None:
    select = SelectList([SelectItem(f"item{i}") for i in range(3)], max_visible=5)
    lines = _lines(select, 40)
    assert len(lines) == 3
    assert "more" not in "".join(lines)


def test_empty_list_shows_no_matches() -> None:
    select = SelectList([SelectItem("a")], max_visible=5)
    select.set_query("nonexistent-query-xyz")
    lines = _lines(select, 40)
    assert len(lines) == 1
    assert "no matches" in lines[0]


def test_selected_row_uses_selected_style() -> None:
    select = SelectList([SelectItem("alpha", "a-desc"), SelectItem("beta", "b-desc")])
    lines = _lines(select, 40)
    # Row 0 (selected) should carry a different SGR run than row 1 (normal).
    assert lines[0] != lines[1]


def test_set_selected_jumps_to_index() -> None:
    select = SelectList([SelectItem(str(i), value=i) for i in range(10)])
    select.set_selected(5)
    assert select.selected_item is not None
    assert select.selected_item.value == 5


def test_set_selected_clamps_out_of_range() -> None:
    select = SelectList([SelectItem(str(i), value=i) for i in range(3)])
    select.set_selected(99)
    assert select.selected_item is not None
    assert select.selected_item.value == 2


def test_selected_bg_fills_full_row_width() -> None:
    from tau.tui.theme import SelectListTheme

    theme = SelectListTheme(selected_bg=Style().with_bg((10, 20, 30)))
    select = SelectList([SelectItem("alpha", "desc")], theme=theme)
    lines = _lines(select, 30)
    assert "\x1b[48;2;10;20;30m" in lines[0]
