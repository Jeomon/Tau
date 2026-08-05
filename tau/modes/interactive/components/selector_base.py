"""Shared bases for the small, non-searchable option pickers in this package.

Five components — extension, oauth, theme, thinking and voice — are the same
widget with a different row source: a callback pair, a cursor, a ``ListState``,
and a ``render_picker_lines`` call. They split into two families by how they
read keys, which is the only reason there are two bases here:

``KeyboundSelector``
    Routes through the ``tui.select.*`` keybinding registry, so the user's
    remapping applies (extension, oauth).

``ArrowSelector``
    Matches raw ``event.key`` for the fixed arrow/enter/escape set (theme,
    thinking, voice). These are opened from slash-commands that predate the
    keybinding registry, and moving them over would change user-visible key
    handling — a behaviour decision, not a refactor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.widgets.list import ListState

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme


class SelectorBase(Component):
    """Cursor, callbacks and theme state common to every picker below."""

    def __init__(
        self,
        on_select: Callable[[Any], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._selected = 0
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._theme = theme or LT()
        self._list_state = ListState()

    def _items(self) -> list[Any]:
        """The selectable rows; an empty list disables movement and confirmation."""
        raise NotImplementedError

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme


class KeyboundSelector(SelectorBase):
    """Picker driven by the ``tui.select.*`` keybindings."""

    #: Accept ``j``/``k`` as extra down/up aliases (vim-style navigation).
    vim_keys: bool = False

    def _confirm_value(self) -> Any:
        """The value handed to ``on_select`` for the row under the cursor."""
        return self._items()[self._selected]

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        kb = get_keybindings()
        count = len(self._items())

        if kb.matches(event, "tui.select.up") or (self.vim_keys and event.key == "k"):
            if count:
                self._selected = max(0, self._selected - 1)
            return True

        if kb.matches(event, "tui.select.down") or (self.vim_keys and event.key == "j"):
            if count:
                self._selected = min(count - 1, self._selected + 1)
            return True

        if kb.matches(event, "tui.select.confirm"):
            if count:
                self._on_select(self._confirm_value())
            return True

        if kb.matches(event, "tui.select.dismiss"):
            self._on_cancel()
            return True

        return False


class ArrowSelector(SelectorBase):
    """Picker driven by raw arrow/enter/escape keys."""

    def _on_move(self) -> None:
        """Called after the cursor actually moves (theme selector previews here)."""

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        items = self._items()
        match event.key:
            case "up":
                if self._selected > 0:
                    self._selected -= 1
                    self._on_move()
            case "down":
                if self._selected < len(items) - 1:
                    self._selected += 1
                    self._on_move()
            case "enter" | "tab":
                if items:
                    self._on_select(items[self._selected])
            case "escape":
                self._on_cancel()
            case _:
                return False
        return True
