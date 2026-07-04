from types import SimpleNamespace
from unittest.mock import Mock

from tau.modes.interactive.components.selector_controller import SelectorController
from tau.modes.interactive.components.settings_selector import SettingItem, SettingsSelector
from tau.tui.components.select_list import InlineSelector
from tau.tui.input import KeyEvent, PasteEvent
from tau.tui.theme import LayoutTheme


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


def test_selector_controller_propagates_theme_to_active_selector() -> None:
    theme = LayoutTheme()
    selector = SimpleNamespace(set_theme=Mock())
    controller = SelectorController(Mock())
    controller.active = InlineSelector(kind="settings", selector=selector)

    controller.set_theme(theme)

    selector.set_theme.assert_called_once_with(theme)


def test_settings_selector_propagates_theme_to_open_submenu() -> None:
    original = LayoutTheme()
    updated = LayoutTheme()
    selector = SettingsSelector(
        [
            SettingItem(
                id="theme",
                label="Theme",
                current_value="dark",
                submenu_items=["dark", "light"],
            )
        ],
        on_change=lambda _item_id, _value: None,
        theme=original,
    )
    selector.activate()

    selector.set_theme(updated)

    assert selector._theme is updated
    assert selector._submenu is not None
    assert selector._submenu._theme is updated


def test_settings_submenu_previews_navigation_and_reverts_on_cancel() -> None:
    previews: list[str] = []
    cancel = Mock()
    render = Mock()
    selector = SettingsSelector(
        [
            SettingItem(
                id="theme",
                label="Theme",
                current_value="dark",
                submenu_items=["dark", "light"],
                submenu_on_preview=previews.append,
                submenu_on_cancel=cancel,
            )
        ],
        on_change=lambda _item_id, _value: None,
    )
    controller = SelectorController(render)
    controller.active = InlineSelector(kind="settings", selector=selector)
    selector.activate()

    controller.handle_input(KeyEvent(key="down"))
    controller.handle_input(KeyEvent(key="escape"))

    assert previews == ["light"]
    cancel.assert_called_once_with()
    assert controller.active is not None
    assert selector.in_submenu is False


def test_settings_submenu_commit_does_not_revert_preview() -> None:
    changes: list[tuple[str, str]] = []
    cancel = Mock()
    selector = SettingsSelector(
        [
            SettingItem(
                id="theme",
                label="Theme",
                current_value="dark",
                submenu_items=["dark", "light"],
                submenu_on_cancel=cancel,
            )
        ],
        on_change=lambda item_id, value: changes.append((item_id, value)),
    )
    selector.activate()
    selector.move_down()

    selector.activate()

    assert changes == [("theme", "light")]
    cancel.assert_not_called()


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
