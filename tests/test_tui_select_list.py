from __future__ import annotations

from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.input import KeyEvent


def _list() -> SelectList[int]:
    return SelectList([SelectItem(label=str(i), value=i) for i in range(10)], max_visible=3)


def test_select_list_page_down_uses_keybinding() -> None:
    select = _list()

    assert select.handle_input(KeyEvent(key="page_down")) is True

    assert select.selected_item is not None
    assert select.selected_item.value == 3


def test_select_list_page_up_uses_keybinding() -> None:
    select = _list()
    select.page_down()
    select.page_down()

    assert select.handle_input(KeyEvent(key="page_up")) is True

    assert select.selected_item is not None
    assert select.selected_item.value == 3


def test_select_list_home_end_use_keybindings() -> None:
    select = _list()

    assert select.handle_input(KeyEvent(key="end")) is True
    assert select.selected_item is not None
    assert select.selected_item.value == 9

    assert select.handle_input(KeyEvent(key="home")) is True
    assert select.selected_item is not None
    assert select.selected_item.value == 0
