"""Tests for TextInput's cursor reporting.

Covers: render(width) publishes the cursor on the component itself (a parent
container offsets it by where the child starts), and the
ctx.ui.set_input_cursor extension point (an arbitrary ANSI-string cursor_cell
override) still works.
"""

from __future__ import annotations

from tau.tui.components.text_input import TextInput
from tests.render_helpers import render_to_lines as _lines


def test_cursor_position_published_on_the_component() -> None:
    ti = TextInput(prefix="> ")
    ti.set_text("hello world")
    ti._cursor = 5

    ti.render(30)

    assert ti.cursor_position is not None
    assert ti.cursor_position.x == 2 + 5  # after "> " plus five characters
    assert ti.cursor_position.y == 0


def test_empty_input_reports_cursor_after_prefix() -> None:
    ti = TextInput(prefix="> ")
    ti.render(30)
    assert ti.cursor_position is not None
    assert ti.cursor_position.x == 2  # after "> "
    assert ti.cursor_position.y == 0


def test_wrapped_multiline_cursor_lands_on_correct_row() -> None:
    ti = TextInput(prefix="> ")
    text = "a very long line that will need to wrap across multiple rows"
    ti.set_text(text)
    ti._cursor = len(text)

    rows = ti.render(20)

    assert len(rows) > 1
    assert ti.cursor_position is not None
    assert ti.cursor_position.y == len(rows) - 1


def test_custom_cursor_cell_extension_point_still_works() -> None:
    """ctx.ui.set_input_cursor installs an arbitrary ANSI-string renderer."""
    ti = TextInput(prefix="> ")
    ti.set_text("x")
    ti._cursor = 1
    ti.cursor_cell = lambda ch: f"\x1b[38;5;199m{ch}\x1b[0m"

    lines = _lines(ti, 30)
    assert "\x1b[38;5;199m" in lines[0]
