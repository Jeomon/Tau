from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.components.simple_picker import DEFAULT_HINT, PickerRow, render_picker_cells
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import apply_style
from tau.tui.widgets.list import ListState

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 10


class ExtensionSelector(Component):
    """
    Generic option picker for extensions.

    Shown when an extension calls ``ctx.select(title, options)`` or
    ``ctx.confirm(title, message)``.  Simple up/down/enter/escape — no search,
    matching ExtensionSelectorComponent behaviour.
    """

    def __init__(
        self,
        title: str,
        options: list[str],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._title = title
        self._options = options
        self._selected = 0
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._theme = theme or LT()
        self._list_state = ListState()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        header = ["  " + apply_style(t.emphasis, line) for line in self._title.splitlines()]
        rows = [PickerRow(opt) for opt in self._options]
        return render_picker_cells(
            buf,
            area,
            header=header,
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            border_style=t.border,
            muted_style=t.muted,
            accent_style=t.accent,
            arrow=t.selector_arrow,
            emphasis_style=t.emphasis,
            hint=DEFAULT_HINT,
            empty_text="No options available",
        )

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        kb = get_keybindings()

        if kb.matches(event, "tui.select.up") or event.key == "k":
            if self._options:
                self._selected = max(0, self._selected - 1)
            return True

        if kb.matches(event, "tui.select.down") or event.key == "j":
            if self._options:
                self._selected = min(len(self._options) - 1, self._selected + 1)
            return True

        if kb.matches(event, "tui.select.confirm"):
            if self._options:
                self._on_select(self._options[self._selected])
            return True

        if kb.matches(event, "tui.select.dismiss"):
            self._on_cancel()
            return True

        return False

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
