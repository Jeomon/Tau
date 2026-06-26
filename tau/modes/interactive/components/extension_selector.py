from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings

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

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = t.border("─" * width)
        lines: list[str] = []

        for line in self._title.splitlines():
            lines.append("  " + t.emphasis(line))
        lines.append(divider)

        if not self._options:
            lines.append("  " + t.muted("No options available"))
        else:
            start = max(
                0,
                min(
                    self._selected - _VISIBLE_ROWS // 2,
                    max(0, len(self._options) - _VISIBLE_ROWS),
                ),
            )
            end = min(start + _VISIBLE_ROWS, len(self._options))

            if start > 0:
                lines.append("  " + t.muted(f"↑ {start} more"))

            for i in range(start, end):
                opt = self._options[i]
                if i == self._selected:
                    lines.append(f"  {t.emphasis(f'→ {opt}')}")
                else:
                    lines.append(f"    {opt}")

            remaining = len(self._options) - end
            if remaining > 0:
                lines.append("  " + t.muted(f"↓ {remaining} more"))

        lines.append(divider)
        lines.append("  " + t.muted("↑↓ navigate  enter select  esc cancel"))

        return lines

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
