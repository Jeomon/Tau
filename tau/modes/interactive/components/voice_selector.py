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


class VoiceSelector(Component):
    """Inline selector for the voices supported by one TTS model."""

    def __init__(
        self,
        model_name: str,
        voices: list[str],
        current: str | None,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._model_name = model_name
        self._voices = list(voices)
        self._current = current
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._theme = theme or LT()
        self._selected = next((i for i, voice in enumerate(self._voices) if voice == current), 0)
        self._list_state = ListState()

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        rows = [
            PickerRow(
                voice,
                [Span(" ", Style()), Span("✓", t.success)] if voice == self._current else [],
            )
            for voice in self._voices
        ]
        return render_picker_cells(
            buf,
            area,
            header=[
                "  " + apply_style(t.emphasis, "Speak Voice"),
                "  " + apply_style(t.muted, self._model_name),
            ],
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
            case "down":
                if self._selected < len(self._voices) - 1:
                    self._selected += 1
            case "enter" | "tab":
                if self._voices:
                    self._on_select(self._voices[self._selected])
            case "escape":
                self._on_cancel()
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme
