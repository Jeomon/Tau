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


class VoiceSelector(ArrowSelector):
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
        super().__init__(on_select, on_cancel, theme)
        self._model_name = model_name
        self._voices = list(voices)
        self._current = current
        self._selected = next((i for i, voice in enumerate(self._voices) if voice == current), 0)

    def _items(self) -> list:
        return self._voices

    def render(self, width: int) -> list[str]:
        t = self._theme
        rows = [
            PickerRow(
                voice,
                [Span(" ", Style()), Span("✓", t.success)] if voice == self._current else [],
            )
            for voice in self._voices
        ]
        return render_picker_lines(
            width,
            header=[
                "  " + apply_style(t.emphasis, "Speak Voice"),
                "  " + apply_style(t.muted, self._model_name),
            ],
            rows=rows,
            selected=self._selected,
            state=self._list_state,
            max_visible=_VISIBLE_ROWS,
            theme=t,
        )
