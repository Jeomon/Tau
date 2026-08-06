"""PageUp/PageDown/Home/End across every picker.

Regression context: ``SelectList`` implemented paging and dispatched it through
the ``tui.select.*`` keybindings, but nothing in the app ever called its
``handle_input``. Every picker either drives the widget by method call from
``SelectorController`` or is a different widget entirely, and none of them
handled the four keys — so PageUp/PageDown/Home/End did nothing anywhere, while
``tests/test_tui_select_list.py`` passed by exercising the widget directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from tau.modes.interactive.components.model_selector import VISIBLE_ROWS, ModelSelector
from tau.modes.interactive.components.selector_controller import SelectorController
from tau.modes.interactive.components.theme_selector import ThemeSelector
from tau.tui.components.multi_select_list import MultiSelectItem, MultiSelectList
from tau.tui.components.select_list import InlineSelector, SelectItem, SelectList
from tau.tui.input import KeyEvent


def _key(name: str) -> KeyEvent:
    return KeyEvent(key=name, char=None)


def _select_list(count: int = 30, max_visible: int = 8) -> SelectList[int]:
    return SelectList(
        [SelectItem(label=str(i), value=i) for i in range(count)],
        max_visible=max_visible,
    )


class TestInlinePickersReachTheSelectList:
    """The generic inline picker path — the one that was entirely dead."""

    def _controller(self, kind: str, selector: object) -> SelectorController:
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind=kind, selector=selector)
        return controller

    def test_page_down_moves_by_one_visible_window(self) -> None:
        selector = _select_list()
        controller = self._controller("prompt", selector)

        assert controller.handle_input(_key("page_down")) is True

        assert selector.selected_item is not None
        assert selector.selected_item.value == 8

    def test_page_up_moves_back(self) -> None:
        selector = _select_list()
        selector.page_down()
        selector.page_down()
        controller = self._controller("prompt", selector)

        assert controller.handle_input(_key("page_up")) is True

        assert selector.selected_item is not None
        assert selector.selected_item.value == 8

    def test_home_and_end_jump_to_the_ends(self) -> None:
        selector = _select_list()
        controller = self._controller("prompt", selector)

        controller.handle_input(_key("end"))
        assert selector.selected_item is not None
        assert selector.selected_item.value == 29

        controller.handle_input(_key("home"))
        assert selector.selected_item is not None
        assert selector.selected_item.value == 0

    def test_paging_clamps_instead_of_wrapping(self) -> None:
        selector = _select_list(count=5)
        controller = self._controller("prompt", selector)

        for _ in range(4):
            controller.handle_input(_key("page_down"))
        assert selector.selected_item is not None
        assert selector.selected_item.value == 4

        for _ in range(4):
            controller.handle_input(_key("page_up"))
        assert selector.selected_item is not None
        assert selector.selected_item.value == 0

    def test_a_selector_without_paging_still_falls_through_to_search(self) -> None:
        """Page keys must not break pickers that never grew the methods."""
        selector = SimpleNamespace(append_search=Mock(), backspace_search=Mock())
        controller = self._controller("model", selector)

        assert controller.handle_input(_key("page_down")) is True

        selector.append_search.assert_not_called()
        selector.backspace_search.assert_not_called()

    def test_search_still_receives_printable_keys(self) -> None:
        selector = SimpleNamespace(append_search=Mock(), backspace_search=Mock())
        controller = self._controller("model", selector)

        controller.handle_input(KeyEvent(key="x", char="x"))

        selector.append_search.assert_called_once_with("x")


class TestModelSelector:
    def _sections(self, count: int = 40) -> list:
        models = [SimpleNamespace(id=f"m{i}", provider="p", context_window=1) for i in range(count)]
        return [("text", "Text", models, "m0")]

    def test_page_down_moves_by_the_render_window(self) -> None:
        modal = ModelSelector(self._sections())

        modal.page_down()

        assert modal._sections[0].selected == VISIBLE_ROWS

    def test_end_then_home(self) -> None:
        modal = ModelSelector(self._sections())

        modal.move_bottom()
        assert modal._sections[0].selected == 39

        modal.move_top()
        assert modal._sections[0].selected == 0

    def test_controller_routes_page_keys_to_the_modal(self) -> None:
        modal = ModelSelector(self._sections())
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind="model", selector=modal)

        assert controller.handle_input(_key("page_down")) is True

        assert modal._sections[0].selected == VISIBLE_ROWS

    def test_tab_still_cycles_sections_rather_than_committing(self) -> None:
        """The page arm must not have displaced the picker-specific Tab meaning."""
        models = [SimpleNamespace(id="m", provider="p", context_window=1)]
        modal = ModelSelector([("text", "Text", models, "m"), ("image", "Image", models, "m")])
        commit = Mock()
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind="model", selector=modal, on_commit=commit)

        controller.handle_input(_key("tab"))

        commit.assert_not_called()
        assert modal._active == 1


class TestArrowSelector:
    """theme / thinking / voice pickers."""

    def _selector(self, count: int = 30) -> ThemeSelector:
        return ThemeSelector(
            names=[f"theme-{i}" for i in range(count)],
            current="theme-0",
            on_select=Mock(),
            on_cancel=Mock(),
            on_preview=Mock(),
        )

    def test_page_down_pages(self) -> None:
        selector = self._selector()

        assert selector.handle_input(_key("page_down")) is True

        assert selector._selected == 10

    def test_end_jumps_to_last(self) -> None:
        selector = self._selector()

        selector.handle_input(_key("end"))

        assert selector._selected == 29

    def test_paging_fires_the_live_preview(self) -> None:
        """The theme picker previews on move; a page jump is a move."""
        preview = Mock()
        selector = ThemeSelector(
            names=[f"theme-{i}" for i in range(30)],
            current="theme-0",
            on_select=Mock(),
            on_cancel=Mock(),
            on_preview=preview,
        )

        selector.handle_input(_key("page_down"))

        preview.assert_called_once_with("theme-10")

    def test_no_preview_when_the_cursor_cannot_move(self) -> None:
        preview = Mock()
        selector = ThemeSelector(
            names=["only"],
            current="only",
            on_select=Mock(),
            on_cancel=Mock(),
            on_preview=preview,
        )

        selector.handle_input(_key("page_down"))

        preview.assert_not_called()


class TestMultiSelectList:
    def _list(self, count: int = 30) -> MultiSelectList:
        return MultiSelectList(
            title="Pick",
            items=[MultiSelectItem(label=str(i)) for i in range(count)],
            on_done=Mock(),
        )

    def test_page_down_then_page_up(self) -> None:
        component = self._list()

        component.handle_input(_key("page_down"))
        assert component._cursor == 10

        component.handle_input(_key("page_up"))
        assert component._cursor == 0

    def test_home_and_end(self) -> None:
        component = self._list()

        component.handle_input(_key("end"))
        assert component._cursor == 29

        component.handle_input(_key("home"))
        assert component._cursor == 0

    def test_paging_clamps_while_arrows_still_wrap(self) -> None:
        component = self._list(count=3)

        component.handle_input(_key("page_down"))
        assert component._cursor == 2
        component.handle_input(_key("page_down"))
        assert component._cursor == 2

        component.handle_input(_key("down"))
        assert component._cursor == 0


class TestResumeSelector:
    def _selector(self, count: int = 30):
        from dataclasses import dataclass
        from datetime import UTC, datetime
        from pathlib import Path

        from tau.modes.interactive.components.session_selector import ResumeSelector

        @dataclass
        class _Session:
            id: str
            path: Path
            modified: datetime
            name: str | None = None
            cwd: Path | None = None
            message_count: int = 0

        sessions = [
            _Session(id=str(i), path=Path(f"/{i}.jsonl"), modified=datetime.now(UTC), name=str(i))
            for i in range(count)
        ]
        return ResumeSelector(
            current_sessions=sessions,
            all_sessions_loader=list,
            max_visible=8,
        )

    def test_page_down_then_page_up(self) -> None:
        selector = self._selector()
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind="resume", selector=selector)

        controller.handle_input(_key("page_down"))
        assert selector._selected == 8

        controller.handle_input(_key("page_up"))
        assert selector._selected == 0

    def test_end_jumps_to_last(self) -> None:
        selector = self._selector()
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind="resume", selector=selector)

        controller.handle_input(_key("end"))

        assert selector._selected == 29

    def test_paging_is_inert_while_a_delete_is_being_confirmed(self) -> None:
        """Matches move_up/move_down, which the delete prompt already freezes."""
        selector = self._selector()
        selector.start_delete()

        selector.page_down()

        assert selector._selected == 0


class TestSettingsSelector:
    def _selector(self, count: int = 30):
        from tau.modes.interactive.components.settings_selector import (
            SettingItem,
            SettingsSelector,
        )

        return SettingsSelector(
            [SettingItem(id=str(i), label=str(i), current_value="v") for i in range(count)],
            on_change=Mock(),
            max_visible=8,
        )

    def test_page_down_then_home(self) -> None:
        selector = self._selector()
        controller = SelectorController(Mock())
        controller.active = InlineSelector(kind="settings", selector=selector)

        controller.handle_input(_key("page_down"))
        assert selector._selected == 8

        controller.handle_input(_key("home"))
        assert selector._selected == 0

    def test_paging_clamps_at_the_last_row(self) -> None:
        selector = self._selector(count=5)

        selector.page_down()
        selector.page_down()

        assert selector._selected == 4


class TestFooterHints:
    """The hint has to name the keys the picker actually accepts."""

    def test_picker_hint_advertises_every_key_it_handles(self) -> None:
        from tau.tui.components.simple_picker import DEFAULT_HINT

        for fragment in ("↑/↓", "PgUp/PgDn", "Home/End", "Enter", "Esc"):
            assert fragment in DEFAULT_HINT

    def test_multi_select_hint_advertises_every_key_it_handles(self) -> None:
        from tau.tui.components.multi_select_list import DEFAULT_HINT

        for fragment in ("↑/↓", "PgUp/PgDn", "Home/End", "Space", "Enter", "Esc"):
            assert fragment in DEFAULT_HINT

    def test_hints_fit_80_columns_without_wrapping(self) -> None:
        """render_picker_lines wraps an over-wide line onto a second row."""
        from tau.tui.components.multi_select_list import DEFAULT_HINT as MULTI_HINT
        from tau.tui.components.simple_picker import DEFAULT_HINT as PICKER_HINT
        from tau.tui.utils import visible_width

        for hint in (PICKER_HINT, MULTI_HINT):
            assert visible_width(hint) + 2 <= 80  # 2 = the leading indent

    def test_login_and_logout_render_the_hint(self) -> None:
        from tau.modes.interactive.components.oauth_selector import (
            OAuthProviderItem,
            OAuthSelector,
        )
        from tau.tui.components.simple_picker import DEFAULT_HINT
        from tau.tui.utils import strip_ansi

        for mode in ("login", "logout"):
            selector = OAuthSelector(
                mode=mode,  # type: ignore[arg-type]
                providers=[OAuthProviderItem(id="oauth", name="Subscription")],
                on_select=Mock(),
                on_cancel=Mock(),
            )
            rendered = [strip_ansi(line) for line in selector.render(80)]
            assert any(DEFAULT_HINT in line for line in rendered), mode
            # One row, not wrapped onto two.
            assert sum(DEFAULT_HINT in line for line in rendered) == 1, mode
