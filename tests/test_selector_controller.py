from types import SimpleNamespace
from unittest.mock import Mock

from tau.modes.interactive.components.selector_controller import SelectorController
from tau.modes.interactive.components.settings_selector import SettingItem, SettingsSelector
from tau.tui.components.select_list import InlineSelector
from tau.tui.input import KeyEvent, PasteEvent


def test_model_selector_commit_closes_modal() -> None:
    render = Mock()
    commit = Mock()
    selector = SimpleNamespace(
        selected_value=lambda: "model",
    )
    controller = SelectorController(render)
    controller.active = InlineSelector(
        kind="model",
        selector=selector,
        on_commit=commit,
    )

    assert controller.handle_input(KeyEvent(key="enter")) is True
    assert controller.is_active is False
    commit.assert_called_once_with("model")
    render.assert_called_once()


def test_selector_paste_routes_to_active_search() -> None:
    render = Mock()
    selector = SimpleNamespace(append_search=Mock())
    controller = SelectorController(render)
    controller.active = InlineSelector(kind="config", selector=selector)

    assert controller.handle_input(PasteEvent(text="secret\r\n")) is True
    selector.append_search.assert_called_once_with("secret\n")
    render.assert_called_once()


def test_settings_selector_space_is_inserted_while_editing_text() -> None:
    render = Mock()
    changes: list[tuple[str, str]] = []
    selector = SettingsSelector(
        [
            SettingItem(
                id="description",
                label="Description",
                current_value="hello",
                text_input=True,
            )
        ],
        on_change=lambda item_id, value: changes.append((item_id, value)),
    )
    controller = SelectorController(render)
    controller.active = InlineSelector(kind="settings", selector=selector)

    controller.handle_input(KeyEvent(key="enter"))
    controller.handle_input(KeyEvent(key=" ", char=" "))
    controller.handle_input(KeyEvent(key="w", char="w"))
    controller.handle_input(KeyEvent(key="enter"))

    assert changes == [("description", "hello w")]
