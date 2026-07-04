"""Tests for TextInput's Stage-4 migration onto render_cells (Buffer-native).

Covers what's new: render_cells sets buf.cursor_position directly, the
legacy render(width) bridge re-embeds CURSOR_MARKER at the same visual
position (needed since Layout still calls render(width) until Stage 5),
and the ctx.ui.set_input_cursor extension point (an arbitrary ANSI-string
cursor_cell override) still works unchanged.
"""

from __future__ import annotations

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.components.text_input import TextInput
from tau.tui.geometry import Rect
from tau.tui.utils import CURSOR_MARKER, visible_width


def _cells(ti: TextInput, width: int) -> tuple[int, Buffer]:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    used = ti.render_cells(Rect(0, 0, width, 0), buf)
    return used, buf


def test_cursor_position_set_on_buffer() -> None:
    ti = TextInput(prefix="> ")
    ti.set_text("hello world")
    ti._cursor = 5

    _used, buf = _cells(ti, 30)

    assert buf.cursor_position is not None
    assert buf.cursor_position == (2 + 5, 0) or (
        buf.cursor_position.x == 7 and buf.cursor_position.y == 0
    )


def test_legacy_render_reembeds_cursor_marker_at_same_column() -> None:
    ti = TextInput(prefix="> ")
    ti.set_text("hello world")
    ti._cursor = 5

    _used, buf = _cells(ti, 30)
    cursor = buf.cursor_position
    assert cursor is not None

    lines = ti.render(30)
    line = lines[cursor.y]
    assert CURSOR_MARKER in line
    before = line[: line.index(CURSOR_MARKER)]
    assert visible_width(before) == cursor.x


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

    lines = ti.render(30)
    assert "\x1b[38;5;199m" in lines[0]


def test_render_and_render_cells_agree() -> None:
    ti = TextInput(prefix="> ")
    ti.set_text("hello world")
    ti._cursor = 5

    used, buf = _cells(ti, 30)
    via_cells = [row_to_ansi(buf, y) for y in range(used)]
    # render() re-embeds CURSOR_MARKER; strip it before comparing since
    # via_cells has no string representation of the cursor position.
    via_render = [line.replace(CURSOR_MARKER, "") for line in ti.render(30)]
    assert via_render == via_cells
