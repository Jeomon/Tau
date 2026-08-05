"""Frame / BufferedTerminal: the double-buffered render loop.

This is the piece that ties the rest of the render layer together:
``BufferedTerminal.draw()`` hands a ``Frame`` to a callback, the callback
calls ``frame.render_widget()`` for each widget (writing into the frame's
``Buffer``), then the previous frame's ``Buffer`` is diffed against the new
one and only the changed cells are sent to the ``Backend``.

Named ``BufferedTerminal`` rather than ``Terminal`` to avoid colliding with
``tau.tui.terminal.Terminal`` (the raw termios/ANSI I/O wrapper this sits on
top of via ``AnsiBackend`` — see ``backend.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tau.tui.backend import Backend
from tau.tui.buffer import _BLANK_CELL, Buffer
from tau.tui.geometry import Position, Rect
from tau.tui.widget import Widget

if TYPE_CHECKING:
    pass


@dataclass(frozen=True, slots=True)
class Fullscreen:
    """The app owns the whole terminal (alt-screen). ``Frame.area`` always starts at (0, 0)."""


@dataclass(frozen=True, slots=True)
class Fixed:
    """A manually-managed region — never auto-resized, call ``BufferedTerminal.resize()``."""

    area: Rect


@dataclass(frozen=True, slots=True)
class Inline:
    """Renders ``height`` rows into the normal scrollback at the current cursor row.

    Everything above the viewport stays real terminal scrollback, the same
    property ``StringRenderer`` relies on for the chat UI.

    The cursor row is fixed at construction rather than dynamically tracked, so
    it doesn't auto-scroll the terminal to keep the viewport visible as content
    grows — the caller is responsible for that.
    """

    height: int
    cursor_row: int = 0


Viewport = Fullscreen | Fixed | Inline


@dataclass(slots=True)
class Frame:
    """The per-draw-call handle widgets render into."""

    buffer: Buffer
    area: Rect
    cursor_position: Position | None = None

    def render_widget(self, widget: Widget, area: Rect | None = None) -> None:
        widget.render(area if area is not None else self.area, self.buffer)

    def set_cursor_position(self, position: Position) -> None:
        self.cursor_position = position


class BufferedTerminal:
    """Own two ``Buffer`` objects and diff them each frame."""

    def __init__(self, backend: Backend, viewport: Viewport | None = None) -> None:
        self._backend = backend
        self._viewport: Viewport = viewport if viewport is not None else Fullscreen()
        area = self._compute_area(backend.size())
        self._buffers = [Buffer.empty(area), Buffer.empty(area)]
        self._current = 0

    @property
    def area(self) -> Rect:
        return self._buffers[self._current].area

    def resize(self, area: Rect) -> None:
        """Manually update a ``Fixed`` viewport's region (never resized automatically)."""
        self._viewport = Fixed(area)
        self._buffers = [Buffer.empty(area), Buffer.empty(area)]
        self._current = 0

    def _compute_area(self, terminal_size: Rect) -> Rect:
        if isinstance(self._viewport, Fixed):
            return self._viewport.area
        if isinstance(self._viewport, Inline):
            height = min(self._viewport.height, terminal_size.height)
            y = min(self._viewport.cursor_row, max(0, terminal_size.height - height))
            return Rect(0, y, terminal_size.width, height)
        return terminal_size

    def _resize_if_needed(self) -> None:
        if isinstance(self._viewport, Fixed):
            return  # Fixed viewports are not automatically resized.
        size = self._compute_area(self._backend.size())
        if size != self._buffers[self._current].area:
            self._buffers = [Buffer.empty(size), Buffer.empty(size)]
            self._current = 0

    def draw(self, render_fn: Callable[[Frame], None]) -> Buffer:
        """Render one frame and flush only the cells that changed to the backend."""
        self._resize_if_needed()
        current = self._buffers[self._current]
        current.content[:] = [_BLANK_CELL] * len(current.content)

        frame = Frame(current, current.area)
        render_fn(frame)

        previous = self._buffers[1 - self._current]
        updates = previous.diff(current)
        if updates:
            self._backend.draw(updates)
        if frame.cursor_position is not None:
            self._backend.set_cursor_position(frame.cursor_position)
        self._backend.flush()

        self._current = 1 - self._current
        return current
