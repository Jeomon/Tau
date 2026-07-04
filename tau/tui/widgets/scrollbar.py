"""Scrollbar/ScrollbarState, mirroring ratatui's ``widgets::Scrollbar``.

Renders into a single-column (vertical) or single-row (horizontal) strip
of a ``Rect`` — typically the last column/row of a panel next to whatever
scrollable content it's tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.widgets.symbols import SCROLLBAR_HORIZONTAL, SCROLLBAR_VERTICAL, ScrollbarSet


class ScrollbarOrientation(Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


@dataclass(slots=True)
class ScrollbarState:
    content_length: int
    position: int = 0
    viewport_length: int = 0

    def scroll_to(self, position: int) -> None:
        max_pos = max(0, self.content_length - max(1, self.viewport_length))
        self.position = max(0, min(position, max_pos))


@dataclass(slots=True)
class Scrollbar:
    orientation: ScrollbarOrientation = ScrollbarOrientation.VERTICAL
    style: Style = field(default_factory=Style)
    thumb_style: Style = field(default_factory=Style)
    show_arrows: bool = True

    def render(self, area: Rect, buf: Buffer, state: ScrollbarState) -> None:
        vertical = self.orientation is ScrollbarOrientation.VERTICAL
        track_len = area.height if vertical else area.width
        if track_len <= 0 or state.content_length <= 0:
            return

        symbols: ScrollbarSet = SCROLLBAR_VERTICAL if vertical else SCROLLBAR_HORIZONTAL
        has_arrows = self.show_arrows and track_len >= 3
        arrow_span = 1 if has_arrows else 0
        inner_len = track_len - 2 * arrow_span

        viewport = max(1, state.viewport_length or inner_len)
        thumb_len = max(1, min(inner_len, round(inner_len * viewport / state.content_length)))
        max_scroll = max(1, state.content_length - viewport)
        max_thumb_pos = inner_len - thumb_len
        thumb_start = round(max_thumb_pos * state.position / max_scroll) if max_scroll else 0
        thumb_start = max(0, min(thumb_start, max_thumb_pos))

        def put(offset: int, glyph: str, style: Style) -> None:
            if vertical:
                buf.set(area.left, area.top + offset, glyph, style)
            else:
                buf.set(area.left + offset, area.top, glyph, style)

        if has_arrows:
            put(0, symbols.begin, self.style)
            put(track_len - 1, symbols.end, self.style)

        for i in range(inner_len):
            on_thumb = thumb_start <= i < thumb_start + thumb_len
            glyph = symbols.thumb if on_thumb else symbols.track
            style = self.thumb_style if on_thumb else self.style
            put(arrow_span + i, glyph, style)
