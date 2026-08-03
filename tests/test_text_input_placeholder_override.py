"""Tests for the transient placeholder override (``dismiss_on_input``).

An extension reporting a transient notice — e.g. the voice extension's
"Voice: mic error" — sets a placeholder override rather than inserting text.
The override is only ever *drawn* while the input is empty, so typing hides it
either way; ``dismiss_on_input=True`` additionally retires it on the first real
keystroke so it cannot reappear when the user deletes back to an empty input.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.components.text_input import TextInput
from tau.tui.geometry import Rect
from tau.tui.input import KeyEvent, PasteEvent

NOTICE = "Voice: mic error"


def _screen(ti: TextInput, width: int = 60) -> str:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    ti.render_cells(Rect(0, 0, width, 0), buf)
    return "\n".join(
        "".join((buf.get(x, y).symbol or " ") for x in range(width)).rstrip()
        for y in range(buf.area.height)
    )


def _type(ti: TextInput, text: str) -> None:
    for ch in text:
        ti.handle_input(KeyEvent(key=ch, char=ch))


def test_override_is_shown_but_never_becomes_real_text() -> None:
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE)

    assert NOTICE in _screen(ti)
    assert ti.text == ""  # a notice, not content the user would submit


def test_typing_hides_override_immediately() -> None:
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE)

    _type(ti, "h")

    screen = _screen(ti)
    assert NOTICE not in screen
    assert "h" in screen
    assert ti.text == "h"


def test_plain_override_reappears_when_input_is_emptied_again() -> None:
    """Default behaviour is unchanged: a standing placeholder comes back."""
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE)

    _type(ti, "h")
    ti.backspace()

    assert NOTICE in _screen(ti)


def test_dismiss_on_input_override_does_not_reappear() -> None:
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE, dismiss_on_input=True)

    _type(ti, "h")
    ti.backspace()

    screen = _screen(ti)
    assert NOTICE not in screen
    assert "Type a message" in screen  # falls back to the configured placeholder


def test_dismiss_on_input_retires_on_any_keystroke_not_just_text() -> None:
    """A navigation key still counts as the user taking over the input."""
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE, dismiss_on_input=True)

    ti.handle_input(KeyEvent(key="left"))

    assert NOTICE not in _screen(ti)


def test_dismiss_on_input_retires_on_paste() -> None:
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE, dismiss_on_input=True)

    ti.handle_input(PasteEvent(text="pasted"))
    ti.set_text("")

    assert NOTICE not in _screen(ti)


def test_dismiss_flag_cleared_when_override_reset() -> None:
    ti = TextInput(prefix="> ", placeholder="Type a message")
    ti.set_placeholder_override(NOTICE, dismiss_on_input=True)
    ti.set_placeholder_override(None)

    # A later plain override must behave like a standing placeholder again.
    ti.set_placeholder_override("standing")
    _type(ti, "h")
    ti.backspace()

    assert "standing" in _screen(ti)
