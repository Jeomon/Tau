"""List/ListState: scrollable item list.

``components/select_list.py``'s ``SelectList`` bakes the item model, fuzzy
filter, key handling, *and* rendering into one class. Here rendering and
selection state are split cleanly: ``ListState`` is
just data (``selected``, ``offset``) the caller owns and mutates on key
events; ``List`` is a stateless renderer for a given item slice + that state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line


class ListDirection(Enum):
    """Which edge the list anchors to when there are fewer items than viewport rows.

    Ordering is always oldest-first/top-to-bottom either way — the
    difference only shows up when content underflows the viewport:
    ``TOP_TO_BOTTOM`` leaves blank rows at the bottom (default);
    ``BOTTOM_TO_TOP`` hugs the bottom edge instead, leaving blank rows at
    the top — e.g. a short chat log that should sit at the bottom of its
    panel rather than float at the top.
    """

    TOP_TO_BOTTOM = auto()
    BOTTOM_TO_TOP = auto()


@dataclass(slots=True)
class ListItem:
    """One entry. ``content`` is a single ``Line``, or several for a tall item.

    Ratatui's ``ListItem`` wraps a ``Text`` (a vector of lines) and reports its
    ``height`` so the list can lay out entries taller than one row — a label
    with a wrapped description underneath, say. This mirrors that: pass a
    ``Line`` for the common case, or a list of them for a multi-row entry, and
    read the normalised rows back from ``lines``.
    """

    content: Line | list[Line]
    style: Style = field(default_factory=Style)

    @staticmethod
    def raw(text: str, style: Style | None = None) -> ListItem:
        return ListItem(Line.raw(text), style or Style())

    @property
    def lines(self) -> list[Line]:
        """The item's rows, always as a list."""
        return self.content if isinstance(self.content, list) else [self.content]

    @property
    def height(self) -> int:
        """Rows this item occupies. Always at least 1 — an empty item still
        takes a row, matching an empty ``Line``."""
        return max(1, len(self.content)) if isinstance(self.content, list) else 1


@dataclass(slots=True)
class ListState:
    selected: int | None = None
    offset: int = 0

    def select(self, index: int | None) -> None:
        self.selected = index

    def select_next(self, count: int) -> None:
        if count == 0:
            self.selected = None
        elif self.selected is None:
            self.selected = 0
        else:
            self.selected = min(self.selected + 1, count - 1)

    def select_previous(self) -> None:
        if self.selected is not None:
            self.selected = max(self.selected - 1, 0)

    def ensure_visible(self, count: int, viewport: int) -> None:
        """Adjust ``offset`` so the selected row stays within the visible window."""
        if self.selected is None or viewport <= 0:
            return
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + viewport:
            self.offset = self.selected - viewport + 1
        self.offset = max(0, min(self.offset, max(0, count - viewport)))

    def snap_to_end(self, count: int, viewport: int) -> None:
        """Scroll to show the last ``viewport`` items — for tail-following a growing list."""
        self.offset = max(0, count - viewport)
        self.selected = max(0, count - 1) if count else None


@dataclass(slots=True)
class List:
    items: list[ListItem] = field(default_factory=list)
    style: Style = field(default_factory=Style)
    highlight_style: Style = field(default_factory=lambda: Style().reversed())
    highlight_symbol: str = "> "
    direction: ListDirection = ListDirection.TOP_TO_BOTTOM

    def render(self, area: Rect, buf: Buffer, state: ListState) -> None:
        if area.is_empty() or not self.items:
            return
        if self.style != Style():
            buf.set_style(area, self.style)

        if any(item.height > 1 for item in self.items):
            self._render_tall(area, buf, state)
            return

        state.ensure_visible(len(self.items), area.height)
        symbol_width = len(self.highlight_symbol)

        last = min(len(self.items), state.offset + area.height)
        visible_count = last - state.offset
        bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
        start_row = area.height - visible_count if bottom_anchored else 0

        for row, idx in enumerate(range(state.offset, last)):
            item = self.items[idx]
            y = area.top + start_row + row
            is_selected = idx == state.selected
            style = self.highlight_style.patch(item.style) if is_selected else item.style
            prefix = self.highlight_symbol if is_selected else " " * symbol_width
            buf.set_string(area.left, y, prefix, style)
            line = item.lines[0].patch_style(style)
            buf.set_line(area.left + symbol_width, y, line, max(0, area.width - symbol_width))
            if is_selected:
                buf.set_style(Rect(area.left, y, area.width, 1), self.highlight_style)

    def _render_tall(self, area: Rect, buf: Buffer, state: ListState) -> None:
        """Layout for lists containing items taller than one row.

        Kept separate from the uniform-height path above so single-row lists —
        every selector in the app today — render through exactly the code they
        always did, byte for byte.

        ``ListState.offset`` counts *items*, not rows, so the offset is walked
        forward here until the selected item fits in the viewport; heights are
        only known at render time, which is why ``ensure_visible`` can't do it.
        """
        symbol_width = len(self.highlight_symbol)
        heights = [item.height for item in self.items]

        offset = max(0, min(state.offset, len(self.items) - 1))
        selected = state.selected
        if selected is not None:
            offset = min(offset, selected)
            # Drop items off the top until the selection's last row fits.
            while offset < selected:
                used = sum(heights[offset : selected + 1])
                if used <= area.height:
                    break
                offset += 1
        state.offset = offset

        rows_used = 0
        placed: list[tuple[int, int, int]] = []  # (item index, top row, rows drawn)
        for idx in range(offset, len(self.items)):
            if rows_used >= area.height:
                break
            drawn = min(heights[idx], area.height - rows_used)
            placed.append((idx, rows_used, drawn))
            rows_used += drawn

        bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
        start_row = area.height - rows_used if bottom_anchored else 0

        for idx, top, drawn in placed:
            item = self.items[idx]
            is_selected = idx == state.selected
            style = self.highlight_style.patch(item.style) if is_selected else item.style
            for row, line in enumerate(item.lines[:drawn]):
                y = area.top + start_row + top + row
                # The cursor symbol marks the item's first row only; its
                # continuation rows are indented to stay aligned under it.
                prefix = self.highlight_symbol if (is_selected and row == 0) else " " * symbol_width
                buf.set_string(area.left, y, prefix, style)
                buf.set_line(
                    area.left + symbol_width,
                    y,
                    line.patch_style(style),
                    max(0, area.width - symbol_width),
                )
                if is_selected:
                    buf.set_style(Rect(area.left, y, area.width, 1), self.highlight_style)
