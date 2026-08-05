from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.modes.interactive.components.selector_base import ArrowSelector
from tau.tui.components.simple_picker import PickerRow, render_picker_lines
from tau.tui.style import Style, apply_style
from tau.tui.text import Span

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
    "ultra": "Beyond max reasoning",
}


class ThinkingSelector(ArrowSelector):
    """Overlay that lets the user pick a ThinkingLevel from a flat bordered list."""

    def __init__(
        self,
        current: ThinkingLevel,
        available: list[ThinkingLevel],
        on_select: Callable[[ThinkingLevel], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        super().__init__(on_select, on_cancel, theme)
        self._current = current
        self._levels = available
        self._selected = next((i for i, lv in enumerate(available) if lv == current), 0)

    def _items(self) -> list:
        return self._levels

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        rows = []
        for lv in self._levels:
            desc = _DESCRIPTIONS.get(lv.value, "")
            spans = [Span("  ", Style()), Span(desc, t.muted)] if desc else []
            if lv == self._current:
                spans.extend([Span(" ", Style()), Span("✓", t.success)])
            rows.append(PickerRow(lv.value, spans))

        return render_picker_lines(
            width,
            header=["  " + apply_style(t.emphasis, "Thinking Effort")],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=len(self._levels) or 1,
            theme=t,
        )
