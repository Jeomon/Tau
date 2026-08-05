"""Block: bordered/titled container.

A Block never wraps another widget by inheritance — it renders its own
border into the outer ``Rect``, then callers ask it for ``.inner(area)`` (a
shrunk ``Rect``) and render their own widget into *that*. This is the same
composition-by-Rect pattern every widget in this package uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, Flag, auto

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.layout import Alignment
from tau.tui.style import Style, apply_style
from tau.tui.text import Line
from tau.tui.utils import visible_width
from tau.tui.widgets.symbols import PLAIN, BorderSet


def _overlay(base: str, text: str, col: int, span: int, total: int) -> str:
    """Place ``text`` onto ``base`` at ``col``, keeping the rest of the row."""
    from tau.tui.compose import composite_line

    return composite_line(base, text, col, span, total)


class Borders(Flag):
    NONE = 0
    TOP = auto()
    BOTTOM = auto()
    LEFT = auto()
    RIGHT = auto()
    ALL = TOP | BOTTOM | LEFT | RIGHT


class TitlePosition(Enum):
    TOP = auto()
    BOTTOM = auto()


@dataclass(slots=True)
class Title:
    """A titled run of text pinned to the top or bottom border, with its own alignment.

    A ``Block`` can carry more than one — e.g. a left-aligned name and a
    right-aligned status badge sharing the top border.
    """

    content: Line
    position: TitlePosition = TitlePosition.TOP
    alignment: Alignment = Alignment.LEFT

    @staticmethod
    def from_like(value: Title | Line | str) -> Title:
        if isinstance(value, Title):
            return value
        return Title(value if isinstance(value, Line) else Line.raw(value))


@dataclass(frozen=True, slots=True)
class Padding:
    """Space left empty *inside* the border, independent of the border itself."""

    left: int = 0
    right: int = 0
    top: int = 0
    bottom: int = 0

    @staticmethod
    def uniform(n: int) -> Padding:
        return Padding(n, n, n, n)

    @staticmethod
    def symmetric(horizontal: int, vertical: int) -> Padding:
        return Padding(horizontal, horizontal, vertical, vertical)


@dataclass(slots=True)
class Block:
    """A border, optionally titled, drawn around the edge of a ``Rect``."""

    borders: Borders = Borders.ALL
    border_set: BorderSet = PLAIN
    border_style: Style = field(default_factory=Style)
    style: Style = field(default_factory=Style)
    padding: Padding = field(default_factory=Padding)
    titles: list[Title] = field(default_factory=list)

    @staticmethod
    def bordered(border_set: BorderSet = PLAIN) -> Block:
        return Block(Borders.ALL, border_set)

    def with_title(
        self,
        title: Title | Line | str,
        position: TitlePosition = TitlePosition.TOP,
        alignment: Alignment = Alignment.LEFT,
    ) -> Block:
        t = Title.from_like(title)
        if not isinstance(title, Title):
            t.position, t.alignment = position, alignment
        self.titles.append(t)
        return self

    def with_padding(self, padding: Padding) -> Block:
        self.padding = padding
        return self

    def inner(self, area: Rect) -> Rect:
        """The ``Rect`` left over once this block's borders and padding are subtracted."""
        top = (1 if Borders.TOP in self.borders else 0) + self.padding.top
        bottom = (1 if Borders.BOTTOM in self.borders else 0) + self.padding.bottom
        left = (1 if Borders.LEFT in self.borders else 0) + self.padding.left
        right = (1 if Borders.RIGHT in self.borders else 0) + self.padding.right
        return Rect(
            area.x + left,
            area.y + top,
            max(0, area.width - left - right),
            max(0, area.height - top - bottom),
        )

    def render_lines(self, width: int, height: int) -> list[str]:
        """Return the frame as ``height`` styled lines of ``width`` columns.

        The interior is blank: a Block draws its own border and the caller
        places content into ``inner(...)``, which is the same
        composition-by-rectangle pattern as before — only the frame is lines
        now, so it can be composited with ``compose.composite_lines`` instead
        of blitted.
        """
        if width <= 0 or height <= 0:
            return []

        b, s = self.border_set, self.border_style
        has_top, has_bottom = Borders.TOP in self.borders, Borders.BOTTOM in self.borders
        has_left, has_right = Borders.LEFT in self.borders, Borders.RIGHT in self.borders

        def styled(text: str) -> str:
            return apply_style(s, text) if text else ""

        rows: list[str] = []
        for y in range(height):
            top_row = has_top and y == 0
            # Matches the reference: a bottom border needs a row of its own, so
            # at height 1 the top border wins and the bottom is not drawn.
            bottom_row = has_bottom and height > 1 and y == height - 1
            if top_row or bottom_row:
                left = (
                    b.top_left
                    if top_row and has_left
                    else (b.bottom_left if bottom_row and has_left else "")
                )
                right = (
                    b.top_right
                    if top_row and has_right
                    else (b.bottom_right if bottom_row and has_right else "")
                )
                fill = max(0, width - visible_width(left) - visible_width(right))
                rows.append(styled(left + b.horizontal * fill + right))
                continue
            left = b.vertical if has_left else ""
            right = b.vertical if has_right and width > 1 else ""
            gap = max(0, width - visible_width(left) - visible_width(right))
            rows.append(styled(left) + " " * gap + styled(right))

        for title in self.titles:
            on_top = title.position is TitlePosition.TOP
            row = 0 if on_top else height - 1
            if not (0 <= row < height):
                continue
            left_inset = 2 if has_left else 1
            right_inset = 1 if has_right else 0
            avail = max(0, (width - right_inset) - left_inset)
            if avail <= 0:
                continue
            from tau.tui.compose import line_to_ansi

            # set_line writes only the title's own columns and leaves the rest
            # of the row alone, so the border either side of it survives.
            # Resolve alignment here rather than letting line_to_ansi pad,
            # which would overwrite border characters with spaces.
            line = Line(list(title.content.spans), title.content.style)
            text = line_to_ansi(line, avail)
            text_width = visible_width(text)
            if title.alignment is Alignment.CENTER:
                offset = max(0, (avail - text_width) // 2)
            elif title.alignment is Alignment.RIGHT:
                offset = max(0, avail - text_width)
            else:
                offset = 0
            if text_width:
                rows[row] = _overlay(rows[row], text, left_inset + offset, text_width, width)
        return rows

    def render(self, area: Rect, buf: Buffer) -> None:
        """Buffer-writing form, for callers still holding one.

        Implemented via ``render_lines`` so there is a single implementation
        rather than two that can drift.
        """
        from tau.tui.ansi_bridge import parse_ansi_into

        if area.is_empty():
            return
        if self.style != Style():
            buf.set_style(area, self.style)
        for i, line in enumerate(self.render_lines(area.width, area.height)):
            parse_ansi_into(buf, area.left, area.top + i, line, area.width)
