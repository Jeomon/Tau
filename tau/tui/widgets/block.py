"""Block: bordered/titled container, mirroring ratatui's ``widgets::Block``.

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
from tau.tui.style import Style
from tau.tui.text import Line
from tau.tui.widgets.symbols import PLAIN, BorderSet


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

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        if self.style != Style():
            buf.set_style(area, self.style)

        b, s = self.border_set, self.border_style
        has_top, has_bottom = Borders.TOP in self.borders, Borders.BOTTOM in self.borders
        has_left, has_right = Borders.LEFT in self.borders, Borders.RIGHT in self.borders

        if has_top:
            buf.set_string(area.left, area.top, b.horizontal * area.width, s)
        if has_bottom and area.height > 1:
            buf.set_string(area.left, area.bottom - 1, b.horizontal * area.width, s)
        if has_left:
            for y in range(area.top, area.bottom):
                buf.set(area.left, y, b.vertical, s)
        if has_right and area.width > 1:
            for y in range(area.top, area.bottom):
                buf.set(area.right - 1, y, b.vertical, s)

        if has_top and has_left:
            buf.set(area.left, area.top, b.top_left, s)
        if has_top and has_right and area.width > 1:
            buf.set(area.right - 1, area.top, b.top_right, s)
        if has_bottom and has_left and area.height > 1:
            buf.set(area.left, area.bottom - 1, b.bottom_left, s)
        if has_bottom and has_right and area.height > 1 and area.width > 1:
            buf.set(area.right - 1, area.bottom - 1, b.bottom_right, s)

        for title in self.titles:
            on_top = title.position is TitlePosition.TOP
            row = area.top if on_top else area.bottom - 1
            if row < area.top or row >= area.bottom:
                continue
            left_inset = 2 if has_left else 1
            right_inset = 1 if has_right else 0
            x = area.left + left_inset
            width = max(0, (area.right - right_inset) - x)
            line = Line(list(title.content.spans), title.content.style, title.alignment)
            buf.set_line(x, row, line, width)
