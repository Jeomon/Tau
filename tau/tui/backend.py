"""Backend: what a Terminal draws through.

``Backend`` is a ``Protocol`` (structural, no inheritance needed) so a
render loop can target either a real terminal or an in-memory one without
knowing which. ``TestBackend`` is the in-memory one — render into it, then
assert on ``backend.buffer`` directly instead of scraping ANSI strings.
``AnsiBackend`` is the real one, adapting Tau's existing ``Terminal``
(raw termios/ANSI I/O in ``terminal.py``) to this protocol.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tau.tui.buffer import Buffer, Cell
from tau.tui.geometry import Position, Rect
from tau.tui.style import OSC8_CLOSE, Style, style_transition

if TYPE_CHECKING:
    from tau.tui.terminal import Terminal


@runtime_checkable
class Backend(Protocol):
    def size(self) -> Rect: ...
    def draw(self, updates: Iterable[tuple[int, int, Cell]]) -> None: ...
    def hide_cursor(self) -> None: ...
    def show_cursor(self) -> None: ...
    def get_cursor_position(self) -> Position: ...
    def set_cursor_position(self, position: Position) -> None: ...
    def clear(self) -> None: ...
    def flush(self) -> None: ...


class TestBackend:
    """Renders into an in-memory Buffer — no PTY, no ANSI, just cell assertions."""

    def __init__(self, width: int, height: int) -> None:
        self._area = Rect(0, 0, width, height)
        self.buffer = Buffer.empty(self._area)
        self.cursor = Position(0, 0)
        self.cursor_hidden = False
        self.flush_count = 0

    def resize(self, width: int, height: int) -> None:
        self._area = Rect(0, 0, width, height)
        self.buffer = Buffer.empty(self._area)

    def size(self) -> Rect:
        return self._area

    def draw(self, updates: Iterable[tuple[int, int, Cell]]) -> None:
        for x, y, cell in updates:
            if self.buffer.area.contains(x, y):
                idx = self.buffer.index_of(x, y)
                self.buffer.content[idx] = Cell(cell.symbol, cell.style, cell.skip)

    def hide_cursor(self) -> None:
        self.cursor_hidden = True

    def show_cursor(self) -> None:
        self.cursor_hidden = False

    def get_cursor_position(self) -> Position:
        return self.cursor

    def set_cursor_position(self, position: Position) -> None:
        self.cursor = position

    def clear(self) -> None:
        self.buffer = Buffer.empty(self._area)

    def flush(self) -> None:
        self.flush_count += 1


class AnsiBackend:
    """Adapts Tau's ``Terminal`` (raw termios/ANSI I/O) to the ``Backend`` protocol."""

    def __init__(self, terminal: Terminal) -> None:
        self._terminal = terminal
        self._cursor = Position(0, 0)

    def size(self) -> Rect:
        return Rect(0, 0, self._terminal.width, self._terminal.height)

    def draw(self, updates: Iterable[tuple[int, int, Cell]]) -> None:
        out: list[str] = []
        active_style: Style | None = None
        cursor_col, cursor_row = -1, -1
        for x, y, cell in updates:
            if cell.skip:
                continue
            if (x, y) != (cursor_col, cursor_row):
                out.append(self._terminal.move_cursor(y, x))
            if cell.style != active_style:
                out.append(style_transition(active_style, cell.style))
                active_style = cell.style
            out.append(cell.symbol or " ")
            cursor_col, cursor_row = x + 1, y
        if active_style is not None and active_style.link:
            out.append(OSC8_CLOSE)
        if out:
            self._terminal.write("".join(out))

    def hide_cursor(self) -> None:
        self._terminal.hide_cursor()

    def show_cursor(self) -> None:
        self._terminal.show_cursor()

    def get_cursor_position(self) -> Position:
        return self._cursor

    def set_cursor_position(self, position: Position) -> None:
        self._cursor = position
        self._terminal.write(self._terminal.move_cursor(position.y, position.x))

    def clear(self) -> None:
        self._terminal.write(self._terminal.clear_screen())

    def flush(self) -> None:
        self._terminal.flush()
