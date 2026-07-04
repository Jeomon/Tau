from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.components.select_list import InlineSelector
from tau.tui.input import InputEvent, KeyEvent, PasteEvent

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme


class SelectorController:
    """Own the lifecycle and input routing for inline selector modals."""

    def __init__(self, request_render: Callable[[], None]) -> None:
        self._active: InlineSelector | None = None
        self._request_render = request_render

    @property
    def active(self) -> InlineSelector | None:
        return self._active

    @active.setter
    def active(self, selector: InlineSelector | None) -> None:
        self._active = selector

    @property
    def is_active(self) -> bool:
        return self._active is not None

    def is_kind(self, kind: str) -> bool:
        return self._active is not None and self._active.kind == kind

    def render(self, width: int) -> list[str]:
        return self._active.render(width) if self._active is not None else []

    def set_theme(self, theme: LayoutTheme) -> None:
        """Apply a theme change to the active selector when supported."""
        if self._active is None:
            return
        setter = getattr(self._active.selector, "set_theme", None)
        if callable(setter):
            setter(theme)

    def handle_input(self, event: InputEvent) -> bool:
        active = self._active
        if active is None:
            return False

        if isinstance(event, PasteEvent):
            appender = getattr(active.selector, "append_search", None)
            if appender is not None:
                appender(event.text.replace("\r", ""))
                self._request_render()
            return True
        if not isinstance(event, KeyEvent):
            return True

        selector = active.selector
        tree = selector if active.kind == "tree" else None
        if tree is not None and getattr(tree, "label_editing", False):
            tree.label_edit_key(event)
            return self._rendered()

        delegated = {"oauth", "extension", "config", "effort", "theme", "voice"}
        if active.kind in delegated:
            selector.handle_input(event)
            return self._rendered()

        if active.kind == "model":
            return self._handle_model(active, event)
        if active.kind == "settings":
            return self._handle_settings(active, event)
        if active.kind == "resume":
            return self._handle_resume(active, event)
        return self._handle_generic(active, event, tree)

    def _handle_model(self, active: InlineSelector, event: KeyEvent) -> bool:
        selector = active.selector
        match event.key:
            case "up":
                selector.move_up()
            case "down":
                selector.move_down()
            case "left":
                selector.toggle_scope()
            case "right":
                selector.toggle_scope()
            case "tab":
                selector.next_section()
            case "enter":
                self._commit(active, selector.selected_value())
            case "escape":
                self._cancel(active)
            case "backspace":
                selector.backspace_search()
            case ch if len(ch) == 1 and ch.isprintable():
                selector.append_search(event.char or ch)
        return self._rendered()

    def _handle_settings(self, active: InlineSelector, event: KeyEvent) -> bool:
        selector = active.selector
        match event.key:
            case "up":
                selector.move_up()
            case "down":
                selector.move_down()
            case "tab":
                selector.next_tab()
            case "enter":
                selector.activate()
            case " ":
                if selector.is_editing:
                    selector.append_search(event.char or " ")
                else:
                    selector.activate()
            case "escape":
                if selector.in_submenu:
                    selector.cancel_submenu()
                else:
                    self._cancel(active)
            case "backspace":
                selector.backspace_search()
            case ch if len(ch) == 1 and ch.isprintable():
                selector.append_search(event.char or ch)
        return self._rendered()

    def _handle_resume(self, active: InlineSelector, event: KeyEvent) -> bool:
        selector = active.selector
        match event.key:
            case "up":
                selector.move_up()
            case "down":
                selector.move_down()
            case "tab":
                selector.toggle_scope()
            case "r" if event.ctrl:
                selector.cycle_sort()
            case "d" if event.ctrl:
                selector.start_delete()
            case "enter":
                if selector.confirming_delete:
                    selector.confirm_delete()
                else:
                    self._commit(active, selector.selected_path())
            case "escape":
                if selector.confirming_delete:
                    selector.cancel_delete()
                else:
                    self._cancel(active)
            case "backspace":
                selector.backspace_search()
            case ch if len(ch) == 1 and ch.isprintable():
                selector.append_search(event.char or ch)
        return self._rendered()

    def _handle_generic(
        self,
        active: InlineSelector,
        event: KeyEvent,
        tree: Any,
    ) -> bool:
        match event.key:
            case "up":
                active.nav(-1)
            case "down":
                active.nav(1)
            case "page_up" if tree is not None:
                tree.page_up()
            case "page_down" if tree is not None:
                tree.page_down()
            case "left" if tree is not None and (event.ctrl or event.alt):
                tree.page_up()
            case "right" if tree is not None and (event.ctrl or event.alt):
                tree.page_down()
            case "left" if tree is not None:
                tree.fold_or_up()
            case "right" if tree is not None:
                tree.unfold_or_down()
            case "enter" | "tab":
                self._commit(active, active.selected_value())
            case "escape":
                self._cancel(active)
            case "d" if event.ctrl and tree is not None:
                tree.set_filter("default")
            case "t" if event.ctrl and tree is not None:
                tree.toggle_filter("no-tools")
            case "u" if event.ctrl and tree is not None:
                tree.toggle_filter("user-only")
            case "l" if event.ctrl and tree is not None:
                tree.toggle_filter("labeled-only")
            case "a" if event.ctrl and tree is not None:
                tree.toggle_filter("all")
            case "f" if event.ctrl and tree is not None:
                tree.cycle_filter()
            case "l" if event.shift and tree is not None:
                tree.start_label_edit()
            case "t" if event.shift and tree is not None:
                tree.toggle_label_timestamps()
            case "backspace" if tree is not None:
                tree.backspace_search()
            case ch if tree is not None and len(ch) == 1 and ch.isprintable():
                tree.append_search(event.char or ch)
        return self._rendered()

    def _commit(self, active: InlineSelector, value: Any) -> None:
        self._active = None
        if value is not None and active.on_commit is not None:
            active.on_commit(value)
        elif active.on_cancel is not None:
            active.on_cancel()

    def _cancel(self, active: InlineSelector) -> None:
        self._active = None
        if active.on_cancel is not None:
            active.on_cancel()

    def _rendered(self) -> bool:
        self._request_render()
        return True
