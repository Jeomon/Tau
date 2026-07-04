from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import apply_style

if TYPE_CHECKING:
    from tau.inference.types import ThinkingLevel
    from tau.tui.theme import LayoutTheme

_DESCRIPTIONS: dict[str, str] = {
    "off": "No reasoning",
    "minimal": "Very brief reasoning (~1k tokens)",
    "low": "Light reasoning (~2k tokens)",
    "medium": "Moderate reasoning (~8k tokens)",
    "high": "Deep reasoning (~16k tokens)",
    "xhigh": "Maximum reasoning (~32k tokens)",
    "max": "Uncapped reasoning",
}


class ThinkingSelector(Component):
    """Overlay that lets the user pick a ThinkingLevel from a flat bordered list."""

    def __init__(
        self,
        current: ThinkingLevel,
        available: list[ThinkingLevel],
        on_select: Callable[[ThinkingLevel], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._current = current
        self._levels = available
        self._selected = next((i for i, lv in enumerate(available) if lv == current), 0)
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._theme = theme or LT()

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = apply_style(t.border, "─" * width)
        lines: list[str] = []

        lines.append("  " + apply_style(t.emphasis, "Thinking Effort"))
        lines.append(divider)

        for i, lv in enumerate(self._levels):
            is_sel = i == self._selected
            is_cur = lv == self._current
            check = f" {apply_style(t.success, '✓')}" if is_cur else ""
            desc = _DESCRIPTIONS.get(lv.value, "")
            desc_part = f"  {apply_style(t.muted, desc)}" if desc else ""

            if is_sel:
                marker = apply_style(t.accent, ">")
                label = apply_style(t.emphasis, lv.value)
                lines.append(f"  {marker} {label}{desc_part}{check}")
            else:
                lines.append(f"    {apply_style(t.muted, lv.value)}{desc_part}{check}")

        lines.append(divider)
        hint = apply_style(t.muted, "↑/↓ to move  ·  Enter to select  ·  Esc to cancel")
        lines.append("  " + hint)

        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        match event.key:
            case "up":
                if self._selected > 0:
                    self._selected -= 1
            case "down":
                if self._selected < len(self._levels) - 1:
                    self._selected += 1
            case "enter" | "tab":
                if self._levels:
                    self._on_select(self._levels[self._selected])
            case "escape":
                self._on_cancel()
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
