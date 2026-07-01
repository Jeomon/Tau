from __future__ import annotations

from unittest.mock import Mock

from tau.modes.interactive.components.voice_selector import VoiceSelector
from tau.tui.input import KeyEvent


def _selector(
    *,
    current: str | None = None,
    on_select: Mock | None = None,
    on_cancel: Mock | None = None,
) -> VoiceSelector:
    return VoiceSelector(
        model_name="TTS-1",
        voices=["alloy", "coral", "nova"],
        current=current,
        on_select=on_select or Mock(),
        on_cancel=on_cancel or Mock(),
    )


def test_render_shows_model_voices_and_current_selection() -> None:
    output = "\n".join(_selector(current="coral").render(80))
    assert "Speak Voice" in output
    assert "TTS-1" in output
    assert "alloy" in output
    assert "coral" in output
    assert "nova" in output
    assert "✓" in output


def test_navigation_and_selection() -> None:
    on_select = Mock()
    selector = _selector(on_select=on_select)
    selector.handle_input(KeyEvent(key="down"))
    selector.handle_input(KeyEvent(key="enter"))
    on_select.assert_called_once_with("coral")


def test_cancel() -> None:
    on_cancel = Mock()
    selector = _selector(on_cancel=on_cancel)
    selector.handle_input(KeyEvent(key="escape"))
    on_cancel.assert_called_once()
