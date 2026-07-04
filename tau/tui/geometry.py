"""Rect: the coordinate type widgets render into.

Mirrors ratatui's ``Rect`` — layout code hands a widget a ``Rect`` describing
its slice of the terminal grid; the widget never sees the full screen.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned region of terminal cells, in absolute (x, y) coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        """Exclusive right edge (``left + width``)."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge (``top + height``)."""
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def inner(
        self,
        margin: int = 0,
        *,
        horizontal: int | None = None,
        vertical: int | None = None,
    ) -> Rect:
        """Return the rect shrunk by a margin on all sides (e.g. for a Block's border)."""
        h = margin if horizontal is None else horizontal
        v = margin if vertical is None else vertical
        w = max(0, self.width - 2 * h)
        ht = max(0, self.height - 2 * v)
        return Rect(self.x + h, self.y + v, w, ht)

    def intersection(self, other: Rect) -> Rect:
        x1, y1 = max(self.left, other.left), max(self.top, other.top)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(x1, y1, max(0, x2 - x1), max(0, y2 - y1))

    def clamp(self, other: Rect) -> Rect:
        """Reposition (and, only if necessary, shrink) this rect to fit inside ``other``.

        Unlike ``intersection`` (which always shrinks to the overlap), this
        keeps the original size whenever it already fits — it only moves
        the rect, e.g. sliding a popup back on-screen after a resize.
        """
        width = min(self.width, other.width)
        height = min(self.height, other.height)
        x = min(max(self.x, other.left), other.right - width)
        y = min(max(self.y, other.top), other.bottom - height)
        return Rect(x, y, width, height)

    def rows(self) -> Iterator[Rect]:
        """Yield each row of this rect as its own 1-tall ``Rect``."""
        for y in range(self.top, self.bottom):
            yield Rect(self.x, y, self.width, 1)

    def columns(self) -> Iterator[Rect]:
        """Yield each column of this rect as its own 1-wide ``Rect``."""
        for x in range(self.left, self.right):
            yield Rect(x, self.y, 1, self.height)

    def positions(self) -> Iterator[Position]:
        """Yield every ``(x, y)`` cell in this rect, row-major."""
        for y in range(self.top, self.bottom):
            for x in range(self.left, self.right):
                yield Position(x, y)

    def offset(self, dx: int = 0, dy: int = 0) -> Rect:
        """Return this rect shifted by ``(dx, dy)`` — same size, new position."""
        return Rect(self.x + dx, self.y + dy, self.width, self.height)


@dataclass(frozen=True, slots=True)
class Position:
    """A bare (x, y) point — distinct from Rect, used for cursor placement.

    Mirrors ratatui's ``Position`` (what ``Frame::set_cursor_position`` takes).
    Tau's ``CURSOR_MARKER`` APC-sequence hack in ``utils.py`` is the ANSI-string
    version of the same concept.
    """

    x: int
    y: int
