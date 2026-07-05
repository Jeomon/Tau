"""Span / Line / Text: the content layer.

Composition is fixed: a ``Text`` holds ``Line``s, a ``Line`` holds ``Span``s,
a ``Span`` is one run of text with one ``Style``. Style at each level is a
patch (see ``Style.patch``) applied on top of the level above when the
content is finally written into a ``Buffer``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import grapheme

from tau.tui.layout import Alignment
from tau.tui.style import Style, Stylize
from tau.tui.utils import grapheme_width

SpanLike = "Span | str"
LineLike = "Line | Span | str"


@dataclass(frozen=True, slots=True)
class Span(Stylize):
    """One contiguous run of text sharing a single style."""

    content: str
    style: Style = Style()

    @staticmethod
    def raw(content: str) -> Span:
        return Span(content)

    @staticmethod
    def styled(content: str, style: Style) -> Span:
        return Span(content, style)

    def patch_style(self, style: Style) -> Span:
        return Span(self.content, self.style.patch(style))

    @property
    def width(self) -> int:
        return sum(
            grapheme_width(cluster)
            for cluster in grapheme.graphemes(self.content)
            if cluster is not None
        )

    @staticmethod
    def from_like(value: Span | str) -> Span:
        return value if isinstance(value, Span) else Span.raw(value)


@dataclass(frozen=True, slots=True)
class Masked:
    """A value that renders as a repeated mask glyph but measures/edits as itself.

    Masks text content while retaining its display width. ``TextInput`` has
    no password-style masking today.
    ``.to_span()`` is what a widget actually renders; ``.value`` stays the
    real content for callers that need it (e.g. to submit the real text).
    """

    value: str
    mask_char: str = "•"

    def to_span(self, style: Style | None = None) -> Span:
        count = sum(1 for _ in grapheme.graphemes(self.value))
        return Span(self.mask_char * count, style if style is not None else Style())


@dataclass(slots=True)
class Line(Stylize):
    """A single row of text: an ordered list of spans plus a base style/alignment."""

    spans: list[Span] = field(default_factory=list)
    style: Style = Style()
    alignment: Alignment | None = None

    @staticmethod
    def raw(content: str) -> Line:
        return Line([Span.raw(content)])

    @staticmethod
    def styled(content: str, style: Style) -> Line:
        return Line([Span.raw(content)], style=style)

    @staticmethod
    def from_spans(spans: Iterable[Span | str]) -> Line:
        return Line([Span.from_like(s) for s in spans])

    def push_span(self, span: Span | str) -> None:
        self.spans.append(Span.from_like(span))

    def patch_style(self, style: Style) -> Line:
        return Line(list(self.spans), self.style.patch(style), self.alignment)

    @property
    def width(self) -> int:
        return sum(s.width for s in self.spans)

    def __iter__(self) -> Iterator[Span]:
        return iter(self.spans)

    @staticmethod
    def from_like(value: Line | Span | str) -> Line:
        if isinstance(value, Line):
            return value
        if isinstance(value, Span):
            return Line([value])
        return Line.raw(value)


@dataclass(slots=True)
class Text(Stylize):
    """Multi-line styled content: an ordered list of lines plus a base style/alignment."""

    lines: list[Line] = field(default_factory=list)
    style: Style = Style()
    alignment: Alignment | None = None

    @staticmethod
    def raw(content: str) -> Text:
        return Text([Line.raw(line) for line in content.split("\n")])

    @staticmethod
    def from_lines(lines: Iterable[Line | Span | str]) -> Text:
        return Text([Line.from_like(line) for line in lines])

    def patch_style(self, style: Style) -> Text:
        return Text(list(self.lines), self.style.patch(style), self.alignment)

    @property
    def width(self) -> int:
        return max((line.width for line in self.lines), default=0)

    @property
    def height(self) -> int:
        return len(self.lines)

    def __iter__(self) -> Iterator[Line]:
        return iter(self.lines)


# Aliases for re-export from tau.tui (the package root already has an
# unrelated Component named `Text`, and `Line` is a common enough name to
# collide with call sites doing `from tau.tui import *`).
TextLine = Line
StyledText = Text
