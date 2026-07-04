from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import apply_style

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

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = apply_style(t.border, "─" * width)
        lines = [
            "  " + apply_style(t.emphasis, "Speak Voice"),
            "  " + apply_style(t.muted, self._model_name),
            divider,
        ]

        count = len(self._voices)
        visible = min(_VISIBLE_ROWS, count)
        start = max(0, min(self._selected - visible // 2, count - visible))

        if start > 0:
            lines.append("  " + apply_style(t.muted, f"↑ {start} more above"))

        for index in range(start, start + visible):
            voice = self._voices[index]
            check = f" {apply_style(t.success, '✓')}" if voice == self._current else ""
            if index == self._selected:
                marker = apply_style(t.accent, ">")
                label = apply_style(t.emphasis, voice)
                lines.append(f"  {marker} {label}{check}")
            else:
                lines.append(f"    {apply_style(t.muted, voice)}{check}")

        remaining = count - (start + visible)
        if remaining > 0:
            lines.append("  " + apply_style(t.muted, f"↓ {remaining} more below"))

        lines.extend(
            [
                divider,
                "  " + apply_style(t.muted, "↑/↓ to move  ·  Enter to select  ·  Esc to cancel"),
            ]
        )
        return lines

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
