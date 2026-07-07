from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.components.simple_picker import DEFAULT_HINT, PickerRow, render_picker_cells
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import Style, apply_style
from tau.tui.text import Span
from tau.tui.widgets.list import ListState

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
        self._list_state = ListState()

    # ── Component ─────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        rows = []
        for lv in self._levels:
            desc = _DESCRIPTIONS.get(lv.value, "")
            spans = [Span("  ", Style()), Span(desc, t.muted)] if desc else []
            if lv == self._current:
                spans.extend([Span(" ", Style()), Span("✓", t.success)])
            rows.append(PickerRow(lv.value, spans))

        return render_picker_cells(
            buf,
            area,
            header=["  " + apply_style(t.emphasis, "Thinking Effort")],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=len(self._levels) or 1,
            border_style=t.border,
            muted_style=t.muted,
            accent_style=t.accent,
            arrow=t.selector_arrow,
            emphasis_style=t.emphasis,
            hint=DEFAULT_HINT,
        )

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
