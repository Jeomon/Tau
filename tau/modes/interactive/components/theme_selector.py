from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.modes.interactive.components.selector_base import ArrowSelector
from tau.tui.components.simple_picker import PickerRow, render_picker_lines
from tau.tui.style import Style, apply_style
from tau.tui.text import Span

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 10


class ThemeSelector(ArrowSelector):
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
        super().__init__(on_select, on_cancel, theme)
        self._names = list(names)
        self._current = current
        self._on_preview = on_preview
        self._selected = next((i for i, n in enumerate(self._names) if n == current), 0)

    def _items(self) -> list:
        return self._names

    def _on_move(self) -> None:
        self._fire_preview()

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        rows = [
            PickerRow(
                name,
                [Span(" ", Style()), Span("✓", t.success)] if name == self._current else [],
            )
            for name in self._names
        ]
        return render_picker_lines(
            width,
            header=["  " + apply_style(t.emphasis, "Theme")],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            theme=t,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fire_preview(self) -> None:
        if self._on_preview is not None and self._names:
            self._on_preview(self._names[self._selected])
