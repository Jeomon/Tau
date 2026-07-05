"""Paragraph: wrapped/scrollable styled text, mirroring ratatui's ``widgets::Paragraph``.

Wrapping runs first (turning each ``Text`` line into 0+ display lines while
preserving per-span style across the break), then ``scroll`` clips the
result — vertically by dropping leading display lines, horizontally by
slicing columns out of what's left. This ordering (wrap, then scroll) is
what makes ``scroll`` behave like a viewport rather than a raw offset into
the unwrapped text, matching ratatui.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import grapheme

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.layout import Alignment
from tau.tui.style import Style
from tau.tui.text import Line, Span, Text
from tau.tui.utils import grapheme_width
from tau.tui.widgets.block import Block


@dataclass(frozen=True, slots=True)
class Wrap:
    trim: bool = False


def _flatten(line: Line) -> list[tuple[str, Style]]:
    """Break a line into (grapheme cluster, style) pairs — never mid-cluster."""
    return [
        (cluster, span.style)
        for span in line
        for cluster in grapheme.graphemes(span.content)
        if cluster is not None
    ]


def _rebuild(chars: list[tuple[str, Style]], alignment: Alignment | None) -> Line:
    spans: list[Span] = []
    run_text, run_style = "", None
    for ch, style in chars:
        if style == run_style:
            run_text += ch
        else:
            if run_text:
                spans.append(Span(run_text, run_style))  # type: ignore[arg-type]
            run_text, run_style = ch, style
    if run_text:
        spans.append(Span(run_text, run_style))  # type: ignore[arg-type]
    return Line(spans, alignment=alignment)


def _rstrip(chars: list[tuple[str, Style]]) -> list[tuple[str, Style]]:
    end = len(chars)
    while end > 0 and chars[end - 1][0] == " ":
        end -= 1
    return chars[:end]


def _wrap_line(line: Line, width: int, trim: bool) -> list[Line]:
    if width <= 0:
        return [line]
    chars = _flatten(line)
    if not chars:
        return [Line([], alignment=line.alignment)]

    out: list[list[tuple[str, Style]]] = []
    current: list[tuple[str, Style]] = []
    col = 0
    last_space = -1  # index in `current` of the most recent space, for word-boundary breaks

    i = 0
    while i < len(chars):
        ch, style = chars[i]
        w = grapheme_width(ch)
        if col + w > width and current:
            if last_space >= 0:
                head, rest = current[: last_space + 1], current[last_space + 1 :]
            else:
                head, rest = current, []
            out.append(_rstrip(head) if trim else head)
            current = rest
            col = sum(grapheme_width(c[0]) for c in rest)
            last_space = -1
            continue
        current.append((ch, style))
        col += w
        if ch == " ":
            last_space = len(current) - 1
        i += 1

    out.append(_rstrip(current) if trim else current)
    return [_rebuild(row, line.alignment) for row in out]


def _hscroll(line: Line, offset: int) -> Line:
    if offset <= 0:
        return line
    chars = _flatten(line)
    col = 0
    i = 0
    while i < len(chars) and col < offset:
        col += grapheme_width(chars[i][0])
        i += 1
    return _rebuild(chars[i:], line.alignment)


@dataclass(slots=True)
class Paragraph:
    text: Text
    block: Block | None = None
    style: Style = field(default_factory=Style)
    alignment: Alignment | None = None
    wrap: Wrap | None = None
    scroll: tuple[int, int] = (0, 0)  # (y, x)

    @staticmethod
    def raw(content: str) -> Paragraph:
        return Paragraph(Text.raw(content))

    def _wrapped_lines(self, width: int) -> list[Line]:
        if self.wrap is None:
            return self.text.lines
        wrapped: list[Line] = []
        for line in self.text.lines:
            wrapped.extend(_wrap_line(line, width, self.wrap.trim))
        return wrapped

    def line_count(self, width: int) -> int:
        """Number of display lines at ``width`` columns, after wrapping — for sizing a scrollbar."""
        return len(self._wrapped_lines(width))

    def line_width(self) -> int:
        """Widest line in the unwrapped source text (ratatui's ``Paragraph::line_width``)."""
        return self.text.width

    def render(self, area: Rect, buf: Buffer) -> None:
        target = area
        if self.block is not None:
            self.block.render(area, buf)
            target = self.block.inner(area)
        if target.is_empty():
            return
        if self.style != Style():
            buf.set_style(target, self.style)

        lines = self._wrapped_lines(target.width)
        scroll_y, scroll_x = self.scroll
        visible = lines[scroll_y : scroll_y + target.height]

        for row, line in enumerate(visible):
            rendered = _hscroll(line, scroll_x) if scroll_x else line
            alignment = rendered.alignment if rendered.alignment is not None else self.alignment
            merged = Line(list(rendered.spans), self.text.style.patch(rendered.style), alignment)
            buf.set_line(target.left, target.top + row, merged, target.width)
