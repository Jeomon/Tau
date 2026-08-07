"""Attachment markers delete whole, in one keystroke.

A marker stands for content the user never typed. Removing one character of
``[file #2]`` leaves ``[file #]`` — no longer resolvable, and indistinguishable
from brackets the user typed themselves. So backspace, delete-forward and
word-delete all treat a marker as a single unit.

``file`` was absent from ``_ATOMIC_TOKEN_END``/``_START`` while image, audio,
video and paste were all present, which made it the one attachment that
backspaced away a character at a time.
"""

from __future__ import annotations

import pytest

from tau.tui.components.text_input import TextInput

# Every marker form the input handler can insert: session-scoped (#N) from
# input_handler's insert_at_cursor calls, and persistent (:{uuid}) from
# _transform_for_history.
_MARKERS = [
    "[image #1]",
    "[image:9f8e7d6c]",
    "[audio #2]",
    "[audio:9f8e7d6c]",
    "[video #3]",
    "[video:9f8e7d6c]",
    "[file #4]",
    "[file:9f8e7d6c]",
    "[paste #5 +12 lines]",
    "[paste #6 340 chars]",
]


def _input(text: str, cursor: int | None = None) -> TextInput:
    field = TextInput()
    field.set_text(text)
    field._cursor = len(text) if cursor is None else cursor
    return field


@pytest.mark.parametrize("marker", _MARKERS)
def test_one_backspace_removes_the_whole_marker(marker: str) -> None:
    field = _input(f"look at {marker}")

    field._backspace()

    assert field.text == "look at "
    assert field._cursor == len("look at ")


@pytest.mark.parametrize("marker", _MARKERS)
def test_one_delete_forward_removes_the_whole_marker(marker: str) -> None:
    field = _input(f"{marker} please", cursor=0)

    field._delete_forward()

    assert field.text == " please"


@pytest.mark.parametrize("marker", _MARKERS)
def test_word_delete_removes_the_whole_marker(marker: str) -> None:
    field = _input(f"look at {marker}")

    field._delete_word_back()

    assert field.text == "look at "


def test_only_the_last_marker_goes() -> None:
    """Two attachments in a row: backspace takes one, not both."""
    field = _input("[file #1][file #2]")

    field._backspace()

    assert field.text == "[file #1]"


def test_ordinary_text_still_deletes_one_character() -> None:
    field = _input("hello")

    field._backspace()

    assert field.text == "hell"


def test_a_malformed_marker_is_not_atomic() -> None:
    """It is ordinary text the user typed; treating it as a unit would surprise."""
    field = _input("[file #]")

    field._backspace()

    assert field.text == "[file #"


def test_a_marker_mid_text_is_untouched_by_a_later_backspace() -> None:
    field = _input("[file #1] and more")

    field._backspace()

    assert field.text == "[file #1] and mor"
