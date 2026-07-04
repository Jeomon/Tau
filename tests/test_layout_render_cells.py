"""Tests for Layout's render-composition moving onto render_cells (Buffer-native).

Layout's editor zone (status lines, dividers, input, pickers, footer) used to
build an intermediate list[str] via string concatenation. render_cells writes
directly into a Buffer instead — Container/TextInput children (already
Buffer-native) get render_cells calls straight through; the remaining
peripheral pickers (SelectorController, TextPrompt — plain classes, not
Component subclasses) still return list[str], parsed in the same way
Component's own default bridge would.
"""

from __future__ import annotations

import asyncio

from tau.modes.interactive.components.layout import Layout
from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.tui import TUI


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
        layout = Layout(tui)
        return tui, layout

    return asyncio.run(_build())


def test_render_and_render_cells_agree_on_content() -> None:
    _tui, layout = _make_layout()
    layout.input.set_text("hello world")

    via_render = layout.render(60)

    buf = Buffer.empty(Rect(0, 0, 60, 0))
    used = layout.render_cells(Rect(0, 0, 60, 0), buf)
    via_cells = [row_to_ansi(buf, y) for y in range(used)]

    # via_render (legacy path) re-embeds CURSOR_MARKER via the default
    # Component bridge; direct render_cells doesn't unless asked — strip it
    # for a content comparison, cursor position is checked separately below.
    from tau.tui.utils import CURSOR_MARKER

    stripped_render = [line.replace(CURSOR_MARKER, "").rstrip() for line in via_render]
    stripped_cells = [line.rstrip() for line in via_cells]
    assert stripped_render == stripped_cells


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
