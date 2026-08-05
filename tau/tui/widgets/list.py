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

from tau.tui.style import Style, apply_style
from tau.tui.text import Line
from tau.tui.utils import truncate_to_width, visible_width


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


def _emit_row(
    runs: list[tuple[str, Style]],
    width: int,
    highlight: Style | None,
) -> str:
    """Flatten styled runs into one line, clipped to ``width``.

    ``highlight`` reproduces ``Buffer.set_style`` over the whole row: the cell
    path patched it onto every cell *after* the content was written, including
    the trailing blanks, which is what makes a selected row read as a solid
    bar. Patch order matches ``Cell.set_style`` — ``existing.patch(highlight)``,
    so the highlight wins where it sets a field.
    """
    out: list[str] = []
    col = 0
    for text, style in runs:
        if col >= width:
            break
        clipped = truncate_to_width(text, width - col)
        if not clipped:
            continue
        out.append(apply_style(style.patch(highlight) if highlight else style, clipped))
        col += visible_width(clipped)
    if highlight is not None and col < width:
        out.append(apply_style(highlight, " " * (width - col)))
    return "".join(out)


def _item_row_runs(
    line: Line,
    base: Style,
    prefix: str,
) -> list[tuple[str, Style]]:
    """The cursor prefix plus the item's spans, each with its resolved style.

    Mirrors ``set_line``: the line's base style sits behind each span's own.
    """
    patched = line.patch_style(base)
    runs: list[tuple[str, Style]] = [(prefix, base)]
    runs.extend((span.content, patched.style.patch(span.style)) for span in patched)
    return runs


@dataclass(slots=True)
class List:
    items: list[ListItem] = field(default_factory=list)
    style: Style = field(default_factory=Style)
    highlight_style: Style = field(default_factory=lambda: Style().reversed())
    highlight_symbol: str = "> "
    direction: ListDirection = ListDirection.TOP_TO_BOTTOM

    def render_lines(self, width: int, height: int, state: ListState) -> list[str]:
        """Return exactly ``height`` styled lines; unused rows are blank.

        Matches the cell path's shape: it wrote into the rows it used and left
        the rest of the area untouched, so blanks here stand for those.
        """
        rows = [""] * max(0, height)
        if width <= 0 or height <= 0 or not self.items:
            return rows

        if any(item.height > 1 for item in self.items):
            return self._render_tall_lines(width, height, state, rows)

        state.ensure_visible(len(self.items), height)
        symbol_width = len(self.highlight_symbol)

        last = min(len(self.items), state.offset + height)
        visible_count = last - state.offset
        bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
        start_row = height - visible_count if bottom_anchored else 0

        for row, idx in enumerate(range(state.offset, last)):
            item = self.items[idx]
            is_selected = idx == state.selected
            style = self.highlight_style.patch(item.style) if is_selected else item.style
            prefix = self.highlight_symbol if is_selected else " " * symbol_width
            runs = _item_row_runs(item.lines[0], style, prefix)
            rows[start_row + row] = _emit_row(
                runs, width, self.highlight_style if is_selected else None
            )
        return rows

    def _render_tall_lines(
        self, width: int, height: int, state: ListState, rows: list[str]
    ) -> list[str]:
        """Layout for lists containing items taller than one row.

        Kept separate from the uniform-height path above so single-row lists —
        every selector in the app today — go through exactly the code they
        always did.

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
                if used <= height:
                    break
                offset += 1
        state.offset = offset

        rows_used = 0
        placed: list[tuple[int, int, int]] = []  # (item index, top row, rows drawn)
        for idx in range(offset, len(self.items)):
            if rows_used >= height:
                break
            drawn = min(heights[idx], height - rows_used)
            placed.append((idx, rows_used, drawn))
            rows_used += drawn

        bottom_anchored = self.direction is ListDirection.BOTTOM_TO_TOP
        start_row = height - rows_used if bottom_anchored else 0

        for idx, top, drawn in placed:
            item = self.items[idx]
            is_selected = idx == state.selected
            style = self.highlight_style.patch(item.style) if is_selected else item.style
            for row, line in enumerate(item.lines[:drawn]):
                # The cursor symbol marks the item's first row only; its
                # continuation rows are indented to stay aligned under it.
                prefix = self.highlight_symbol if (is_selected and row == 0) else " " * symbol_width
                rows[start_row + top + row] = _emit_row(
                    _item_row_runs(line, style, prefix),
                    width,
                    self.highlight_style if is_selected else None,
                )
        return rows
