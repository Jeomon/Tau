from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.modes.interactive.components.selector_base import KeyboundSelector
from tau.tui.components.simple_picker import PickerRow, render_picker_lines
from tau.tui.style import apply_style

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 10


class ExtensionSelector(KeyboundSelector):
    """
    Generic option picker for extensions.

    Shown when an extension calls ``ctx.select(title, options)`` or
    ``ctx.confirm(title, message)``.  Simple up/down/enter/escape — no search,
    matching ExtensionSelectorComponent behaviour.
    """

    vim_keys = True

    def __init__(
        self,
        title: str,
        options: list[str],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        super().__init__(on_select, on_cancel, theme)
        self._title = title
        self._options = options

    def _items(self) -> list:
        return self._options

    def _confirm_value(self) -> str:
        return self._options[self._selected]

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        t = self._theme
        header = ["  " + apply_style(t.emphasis, line) for line in self._title.splitlines()]
        rows = [PickerRow(opt) for opt in self._options]
        return render_picker_lines(
            width,
            header=header,
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            theme=t,
            empty_text="No options available",
        )
