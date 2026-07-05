"""Tests for TextInput's render_cells (Buffer-native) rendering.

Covers: render_cells sets buf.cursor_position directly, and the
ctx.ui.set_input_cursor extension point (an arbitrary ANSI-string cursor_cell
override) still works.
"""

from __future__ import annotations

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.components.text_input import TextInput
from tau.tui.geometry import Rect


def _cells(ti: TextInput, width: int) -> tuple[int, Buffer]:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    used = ti.render_cells(Rect(0, 0, width, 0), buf)
    return used, buf


def _lines(ti: TextInput, width: int) -> list[str]:
    used, buf = _cells(ti, width)
    return [row_to_ansi(buf, y) for y in range(used)]


def test_cursor_position_set_on_buffer() -> None:
    ti = TextInput(prefix="> ")
    ti.set_text("hello world")
    ti._cursor = 5

    _used, buf = _cells(ti, 30)

    assert buf.cursor_position is not None
    assert buf.cursor_position == (2 + 5, 0) or (
        buf.cursor_position.x == 7 and buf.cursor_position.y == 0
    )


def test_empty_input_reports_cursor_after_prefix() -> None:
    ti = TextInput(prefix="> ")
    _used, buf = _cells(ti, 30)
    assert buf.cursor_position is not None
    assert buf.cursor_position.x == 2  # after "> "
    assert buf.cursor_position.y == 0


def test_wrapped_multiline_cursor_lands_on_correct_row() -> None:
    ti = TextInput(prefix="> ")
    text = "a very long line that will need to wrap across multiple rows"
    ti.set_text(text)
    ti._cursor = len(text)

    used, buf = _cells(ti, 20)
    assert used > 1
    assert buf.cursor_position is not None
    assert buf.cursor_position.y == used - 1


def test_custom_cursor_cell_extension_point_still_works() -> None:
    """ctx.ui.set_input_cursor installs an arbitrary ANSI-string renderer."""
    ti = TextInput(prefix="> ")
    ti.set_text("x")
    ti._cursor = 1
    ti.cursor_cell = lambda ch: f"\x1b[38;5;199m{ch}\x1b[0m"

    lines = _lines(ti, 30)
    assert "\x1b[38;5;199m" in lines[0]
