from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 10


class ThemeSelector(Component):
    """Overlay for picking a color theme with live preview on navigation."""

    def __init__(
        self,
        names: list[str],
        current: str,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        on_preview: Callable[[str], None] | None = None,
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._names = list(names)
        self._current = current
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._on_preview = on_preview
        self._theme = theme or LT()
        self._selected = next((i for i, n in enumerate(self._names) if n == current), 0)

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = t.border("─" * width)
        lines: list[str] = []

        lines.append("  " + t.emphasis("Theme"))
        lines.append(divider)

        count = len(self._names)
        visible = min(_VISIBLE_ROWS, count)
        start = max(0, min(self._selected - visible // 2, count - visible))

        if start > 0:
            lines.append("  " + t.muted(f"↑ {start} more above"))

        for i in range(start, start + visible):
            name = self._names[i]
            check = f" {t.success('✓')}" if name == self._current else ""
            if i == self._selected:
                lines.append(f"  {t.accent('>')} {t.emphasis(name)}{check}")
            else:
                lines.append(f"    {t.muted(name)}{check}")

        remaining = count - (start + visible)
        if remaining > 0:
            lines.append("  " + t.muted(f"↓ {remaining} more below"))

        lines.append(divider)
        lines.append("  " + t.muted("↑/↓ to move  ·  Enter to select  ·  Esc to cancel"))

        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        match event.key:
            case "up":
                if self._selected > 0:
                    self._selected -= 1
                    self._fire_preview()
            case "down":
                if self._selected < len(self._names) - 1:
                    self._selected += 1
                    self._fire_preview()
            case "enter" | "tab":
                if self._names:
                    self._on_select(self._names[self._selected])
            case "escape":
                self._on_cancel()
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fire_preview(self) -> None:
        if self._on_preview is not None and self._names:
            self._on_preview(self._names[self._selected])
