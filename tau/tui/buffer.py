"""Buffer / Cell: the grid widgets render into, mirroring ratatui's ``buffer`` module.

This is the piece that was missing: ``Component.render()`` today returns
``list[str]`` (ANSI baked into the string), and the renderer's ``_diff_row``
(``tui.py``) has to re-parse those strings back into ``(symbol, style)``
cells before it can diff two frames. A ``Buffer`` is that cell grid *live* —
widgets write structured ``Span``/``Line`` content directly into it, so
nothing needs to be parsed back out; ``Buffer.diff`` operates on real
``Cell`` objects the whole way through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import grapheme

from tau.tui.geometry import Position, Rect
from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import grapheme_width


@dataclass(slots=True)
class Cell:
    """One terminal character position: a glyph plus its resolved style.

    ``skip`` marks a cell the diff/backend must leave untouched — e.g. the
    region under a Kitty/iTerm2 inline image (see ``components/image.py``),
    where the terminal itself owns those pixels and a stray SGR write would
    corrupt them.
    """

    symbol: str = " "
    style: Style = Style()
    skip: bool = False

    def set_symbol(self, symbol: str) -> None:
        self.symbol = symbol or " "

    def set_style(self, style: Style) -> None:
        self.style = self.style.patch(style)

    def reset(self) -> None:
        self.symbol, self.style, self.skip = " ", Style(), False


@dataclass(slots=True)
class RawWrite:
    """Content that must bypass normal cell diffing/printing entirely.

    e.g. a Kitty/iTerm2 inline image escape sequence — its payload isn't
    printable text, and the terminal (not this process) owns the pixels
    once drawn, so it must never be re-parsed as cells or re-sent just
    because a neighboring cell changed. ``token`` identifies this write's
    content cheaply for change detection (the escape ``data`` itself may
    be a huge base64 blob) — the renderer resends only when ``token``
    differs from what it last sent at this position.
    """

    x: int
    y: int
    data: str
    token: str


@dataclass(slots=True)
class Buffer:
    """A ``Rect``-shaped grid of ``Cell``s — the render target every ``Widget`` writes into."""

    area: Rect
    content: list[Cell] = field(default_factory=list)
    # Set by whichever component owns the current text cursor (e.g. a
    # focused input) — mirrors ratatui's Frame.cursor_position. The legacy
    # bridge (ansi_bridge.parse_ansi_into) also populates this from a
    # CURSOR_MARKER embedded in old-contract ANSI output, so cursor
    # positioning keeps working for components not yet on render_cells.
    cursor_position: Position | None = None
    # See RawWrite. Populated by components like Image whose content can't
    # be represented as printable Cells.
    raw_writes: list[RawWrite] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.content:
            self.content = [Cell() for _ in range(max(0, self.area.area))]

    @classmethod
    def empty(cls, area: Rect) -> Buffer:
        return cls(area)

    def grow_to(self, height: int) -> None:
        """Extend this buffer downward to at least ``height`` rows of blank cells.

        Used for content whose row count isn't known until it's been rendered
        (the live scrollback content tree) instead of a fixed on-screen grid.
        No-op if already at least ``height`` rows tall.
        """
        if height <= self.area.height:
            return
        added = (height - self.area.height) * self.area.width
        self.content.extend(Cell() for _ in range(added))
        self.area = Rect(self.area.x, self.area.y, self.area.width, height)

    def index_of(self, x: int, y: int) -> int:
        if not self.area.contains(x, y):
            raise IndexError(f"({x}, {y}) outside buffer area {self.area}")
        return (y - self.area.y) * self.area.width + (x - self.area.x)

    def get(self, x: int, y: int) -> Cell:
        return self.content[self.index_of(x, y)]

    def set(self, x: int, y: int, symbol: str, style: Style | None = None) -> None:
        if not self.area.contains(x, y):
            return
        cell = self.content[self.index_of(x, y)]
        cell.symbol = symbol or " "
        cell.style = style if style is not None else Style()

    def set_string(
        self,
        x: int,
        y: int,
        text: str,
        style: Style | None = None,
        max_width: int | None = None,
    ) -> int:
        """Write ``text`` starting at ``(x, y)``; returns the column after the last glyph written.

        Iterates whole grapheme clusters (not raw ``char``s) so a combining
        accent or a ZWJ emoji sequence lands in one cell instead of getting
        split across several. Double-width clusters occupy an extra
        continuation cell (empty symbol) so column math stays exact,
        matching ratatui's ``set_stringn``.
        """
        limit = self.area.right if max_width is None else min(self.area.right, x + max_width)
        col = x
        for cluster in grapheme.graphemes(text):
            w = grapheme_width(cluster)
            if w == 0:
                continue
            if col + w > limit:
                break
            self.set(col, y, cluster, style)
            if w == 2 and col + 1 < limit:
                self.set(col + 1, y, "", style)
            col += w
        return col

    def set_span(self, x: int, y: int, span: Span, max_width: int | None = None) -> int:
        return self.set_string(x, y, span.content, span.style, max_width)

    def set_line(self, x: int, y: int, line: Line, width: int) -> None:
        """Write a line's spans into row ``y``, resolving its alignment within ``width`` columns."""
        content_width = line.width
        start = x
        if line.alignment is Alignment.CENTER:
            start = x + max(0, (width - content_width) // 2)
        elif line.alignment is Alignment.RIGHT:
            start = x + max(0, width - content_width)

        cur = start
        end = x + width
        for span in line:
            if cur >= end:
                break
            merged = Span(span.content, line.style.patch(span.style))
            cur = self.set_span(cur, y, merged, max_width=end - cur)

    def set_style(self, area: Rect, style: Style) -> None:
        """Patch ``style`` onto every cell in ``area`` (e.g. painting a background)."""
        target = self.area.intersection(area)
        for yy in range(target.top, target.bottom):
            for xx in range(target.left, target.right):
                self.get(xx, yy).set_style(style)

    def diff(self, other: Buffer) -> list[tuple[int, int, Cell]]:
        """Return the ``(x, y, cell)`` triples where ``other`` differs from ``self``.

        Both buffers must share the same area. Mirrors ratatui's
        ``Buffer::diff``: a run of cells is invalidated one extra step past
        any cell whose glyph width changed, so a shrinking/growing
        double-width glyph never leaves a stale trailing cell on screen.
        """
        if self.area != other.area:
            raise ValueError("diff requires buffers of the same area")
        w = self.area.width
        updates: list[tuple[int, int, Cell]] = []
        invalidated = 0
        to_skip = 0
        for i, (prev, cur) in enumerate(zip(self.content, other.content, strict=True)):
            if to_skip == 0 and (cur != prev or invalidated > 0):
                x = self.area.x + i % w
                y = self.area.y + i // w
                updates.append((x, y, cur))
            cur_w = grapheme_width(cur.symbol) if cur.symbol else 1
            prev_w = grapheme_width(prev.symbol) if prev.symbol else 1
            to_skip = max(cur_w - 1, 0)
            invalidated = max(cur_w, prev_w, invalidated) - 1
        return updates
