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
        self._list_state = ListState()

    # ── Component ─────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        rows = [
            PickerRow(
                name,
                [Span(" ", Style()), Span("✓", t.success)] if name == self._current else [],
            )
            for name in self._names
        ]
        return render_picker_cells(
            buf,
            area,
            header=["  " + apply_style(t.emphasis, "Theme")],
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
        )

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
