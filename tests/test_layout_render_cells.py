"""Tests for Layout's render composition on render_cells (Buffer-native).

Layout's editor zone (status lines, dividers, input, pickers, footer) writes
directly into a Buffer via render_cells — Container/TextInput/pickers all
write straight into it with no intermediate list[str] round trip.
"""

from __future__ import annotations

import asyncio

from tau.modes.interactive.components.layout import Layout
from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.service import TUI


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


def _make_layout() -> tuple[TUI, Layout]:
    async def _build() -> tuple[TUI, Layout]:
        term = FakeTerminal()
        tui = TUI(terminal=term)  # type: ignore[arg-type]
        # Cursor blink starts an asyncio task lazily on first render_cells
        # call; these tests call render_cells synchronously outside any
        # running loop, so keep blink off to avoid a timing-dependent
        # "no running event loop" error unrelated to what's under test.
        layout = Layout(tui, cursor_blink=False)
        return tui, layout

    return asyncio.run(_build())


def test_render_cells_produces_content() -> None:
    _tui, layout = _make_layout()
    layout.input.set_text("hello world")

    buf = Buffer.empty(Rect(0, 0, 60, 0))
    used = layout.render_cells(Rect(0, 0, 60, 0), buf)
    lines = [row_to_ansi(buf, y).rstrip() for y in range(used)]

    assert used > 0
    assert any("hello world" in line for line in lines)


def test_editor_row_bookkeeping_matches_cursor_position() -> None:
    _tui, layout = _make_layout()
    layout.input.set_text("hello world")

    buf = Buffer.empty(Rect(0, 0, 60, 0))
    layout.render_cells(Rect(0, 0, 60, 0), buf)

    assert buf.cursor_position is not None
    assert buf.cursor_position.y == layout._editor_row
    # "❯ " prefix (2 cols) + "hello world" (11 chars) = column 13
    assert buf.cursor_position.x == 13


def test_modal_hides_input_and_zeroes_editor_row_count() -> None:
    _tui, layout = _make_layout()
    layout._prompt.open("Enter value", on_commit=lambda v: None, on_cancel=lambda: None)

    buf = Buffer.empty(Rect(0, 0, 60, 0))
    used = layout.render_cells(Rect(0, 0, 60, 0), buf)

    assert layout._editor_row_count == 0
    assert used > 0


def test_long_status_line_wraps_and_reflows_on_resize() -> None:
    _tui, layout = _make_layout()
    content = "status-" + ("x" * 60) + "-tail"
    layout._status_map["extension"] = content

    def status_lines(width: int) -> list[str]:
        from tau.tui.utils import strip_ansi

        buf = Buffer.empty(Rect(0, 0, width, 0))
        used = layout.render_cells(Rect(0, 0, width, 0), buf)
        lines = [strip_ansi(row_to_ansi(buf, y)).rstrip() for y in range(used)]
        divider_index = next(index for index, line in enumerate(lines) if "─" in line)
        return lines[:divider_index]

    narrow = status_lines(20)
    wide = status_lines(40)

    assert len(narrow) > len(wide) > 1
    assert all(len(line) <= 20 for line in narrow)
    assert all(len(line) <= 40 for line in wide)
    assert content in "".join(line.strip() for line in narrow)
    assert content in "".join(line.strip() for line in wide)
