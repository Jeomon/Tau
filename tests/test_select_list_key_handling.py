"""Keys pressed in a `ui.select` picker must not raise out of the stdin callback.

`open_tree_selector` builds a plain `SelectList` but labels it `kind="tree"` —
the same kind `open_branch_tree_selector` uses for a real `TreeSelectList`.
`SelectorController` trusted the label, so a permission prompt, `ui.confirm`,
or any extension picker received tree-only calls:

    left arrow  -> tree.fold_or_up()      AttributeError
    any letter  -> selector.append_search AttributeError
    backspace   -> selector.backspace_search AttributeError

Each raised out of `TUI._on_stdin_ready`, where only asyncio's default handler
saw it. The keystroke was swallowed and nothing appeared on screen. One real
session logged 42 of these in a day, all invisible.

Two fixes, and the tests pin both: the tree branch is gated on the capability
rather than the label, and `SelectList` gained the search methods it always had
the filtering for.
"""

from __future__ import annotations

from typing import Any

import pytest

from tau.modes.interactive.components.selector_controller import SelectorController
from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.input import KeyEvent

_OPTIONS = ["Allow Once", "Allow for this session (uname*)", "Deny"]


def _picker() -> SelectList[str]:
    return SelectList([SelectItem(label=o, value=o) for o in _OPTIONS], max_visible=8)


def _controller(selector: Any, kind: str = "tree") -> tuple[Any, list[Any]]:
    committed: list[Any] = []

    class _Active:
        def __init__(self) -> None:
            self.kind = kind
            self.selector = selector

        def nav(self, direction: int) -> None:
            selector.move_up() if direction < 0 else selector.move_down()

        def selected_value(self) -> Any:
            item = selector.selected_item
            return item.value if item else None

    controller = SelectorController.__new__(SelectorController)
    controller._active = _Active()
    controller._request_render = lambda: None
    controller._rendered = lambda: True
    controller._commit = lambda active, value: committed.append(value)
    controller._cancel = lambda active: committed.append(None)
    return controller, committed


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("printable character", KeyEvent(key="d", char="d")),
        ("backspace", KeyEvent(key="backspace")),
        ("left arrow", KeyEvent(key="left")),
        ("right arrow", KeyEvent(key="right")),
        ("ctrl+d", KeyEvent(key="d", ctrl=True)),
        ("ctrl+t", KeyEvent(key="t", ctrl=True)),
        ("ctrl+u", KeyEvent(key="u", ctrl=True)),
        ("page up", KeyEvent(key="page_up")),
        ("page down", KeyEvent(key="page_down")),
    ],
)
def test_no_key_raises_in_a_plain_picker(name: str, event: KeyEvent) -> None:
    controller, _ = _controller(_picker())

    controller.handle_input(event)  # must not raise


def test_typing_filters_the_options() -> None:
    """The fuzzy filter was always there; nothing could feed it a keystroke."""
    picker = _picker()
    controller, _ = _controller(picker)

    controller.handle_input(KeyEvent(key="D", char="D"))
    controller.handle_input(KeyEvent(key="e", char="e"))
    controller.handle_input(KeyEvent(key="n", char="n"))

    assert [item.label for item in picker._filtered] == ["Deny"]


def test_backspace_widens_the_filter_again() -> None:
    picker = _picker()
    controller, _ = _controller(picker)
    for char in "Deny":
        controller.handle_input(KeyEvent(key=char, char=char))

    controller.handle_input(KeyEvent(key="backspace"))
    controller.handle_input(KeyEvent(key="backspace"))
    controller.handle_input(KeyEvent(key="backspace"))
    controller.handle_input(KeyEvent(key="backspace"))

    assert [item.label for item in picker._filtered] == _OPTIONS


def test_arrows_and_enter_still_work() -> None:
    picker = _picker()
    controller, committed = _controller(picker)

    controller.handle_input(KeyEvent(key="down"))
    controller.handle_input(KeyEvent(key="enter"))

    assert committed == ["Allow for this session (uname*)"]


def test_escape_still_cancels() -> None:
    controller, committed = _controller(_picker())

    controller.handle_input(KeyEvent(key="escape"))

    assert committed == [None]


def test_a_real_tree_selector_still_gets_tree_keys() -> None:
    """The capability gate must not disable folding for the branch picker."""
    calls: list[str] = []

    class _Tree:
        selected_item = SelectItem(label="x", value="x")

        def fold_or_up(self) -> None:
            calls.append("fold_or_up")

        def unfold_or_down(self) -> None:
            calls.append("unfold_or_down")

        def move_up(self) -> None: ...
        def move_down(self) -> None: ...

    controller, _ = _controller(_Tree())

    controller.handle_input(KeyEvent(key="left"))
    controller.handle_input(KeyEvent(key="right"))

    assert calls == ["fold_or_up", "unfold_or_down"]


def test_search_methods_exist_on_the_component_itself() -> None:
    """SelectorController calls these by name on every printable key."""
    picker = _picker()

    picker.append_search("De")
    assert [item.label for item in picker._filtered] == ["Deny"]

    picker.backspace_search()
    picker.backspace_search()
    assert [item.label for item in picker._filtered] == _OPTIONS
