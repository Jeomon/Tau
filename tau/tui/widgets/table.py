"""Table/TableState: rows/columns with a header.

Column sizing is delegated to ``Layout`` (real ``Flex`` support, one solver
instead of two) rather than the hand-rolled width math this used to carry.
Tau's existing ``int``/``"NN%"``/``None`` vocabulary is kept as the public
spec — it's translated to ``Constraint.length``/``.percentage``/``.fill``
before splitting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.layout import Constraint, Direction, Flex, Layout
from tau.tui.style import Style
from tau.tui.text import Line, Text


@dataclass(slots=True)
class Row:
    cells: list[Text]
    height: int | None = None
    bottom_margin: int = 0
    style: Style = field(default_factory=Style)

    @staticmethod
    def raw(
        *cells: str,
        style: Style | None = None,
        height: int | None = None,
        bottom_margin: int = 0,
    ) -> Row:
        return Row([Text.raw(c) for c in cells], height, bottom_margin, style or Style())

    @property
    def resolved_height(self) -> int:
        if self.height is not None:
            return max(1, self.height)
        return max(1, max((cell.height for cell in self.cells), default=1))


def _to_constraint(spec: int | str | None) -> Constraint:
    if spec is None:
        return Constraint.fill(1)
    if isinstance(spec, str) and spec.strip().endswith("%"):
        return Constraint.percentage(float(spec.strip()[:-1]))
    return Constraint.length(int(spec))


@dataclass(slots=True)
class TableState:
    selected: int | None = None
    offset: int = 0


@dataclass(slots=True)
class Table:
    rows: list[Row]
    widths: list[int | str | None]
    header: Row | None = None
    column_gap: int = 1
    flex: Flex = Flex.LEGACY
    style: Style = field(default_factory=Style)
    header_style: Style = field(default_factory=lambda: Style().bold())
    highlight_style: Style = field(default_factory=lambda: Style().reversed())

    def _column_rects(self, area: Rect) -> list[Rect]:
        constraints = [_to_constraint(w) for w in self.widths]
        row_area = Rect(area.left, area.top, area.width, 1)
        layout = Layout(Direction.HORIZONTAL, constraints, spacing=self.column_gap, flex=self.flex)
        return layout.split(row_area)

    def render(self, area: Rect, buf: Buffer, state: TableState | None = None) -> None:
        if area.is_empty():
            return
        if self.style != Style():
            buf.set_style(area, self.style)

        cols = self._column_rects(area)
        y = area.top

        if self.header is not None:
            self._render_row(buf, y, cols, self.header, self.header_style)
            y += self.header.resolved_height + self.header.bottom_margin

        state = state or TableState()
        body_top = y
        self._scroll_into_view(state, area.bottom - body_top)

        for row_idx in range(state.offset, len(self.rows)):
            row = self.rows[row_idx]
            if y + row.resolved_height > area.bottom:
                break
            is_selected = row_idx == state.selected
            style = self.highlight_style.patch(row.style) if is_selected else row.style
            self._render_row(buf, y, cols, row, style)
            y += row.resolved_height + row.bottom_margin

    def _scroll_into_view(self, state: TableState, viewport: int) -> None:
        if state.selected is None:
            return
        if state.selected < state.offset:
            state.offset = state.selected
            return
        for _ in range(len(self.rows) + 1):
            consumed, fits = 0, False
            for idx in range(state.offset, len(self.rows)):
                h = self.rows[idx].resolved_height
                if consumed + h > viewport:
                    break
                if idx == state.selected:
                    fits = True
                    break
                consumed += h + self.rows[idx].bottom_margin
            if fits or state.offset >= state.selected:
                return
            state.offset += 1

    def _render_row(self, buf: Buffer, y: int, cols: list[Rect], row: Row, style: Style) -> None:
        height = row.resolved_height
        for col, cell in zip(cols, row.cells, strict=False):
            if col.width <= 0:
                continue
            for line_idx in range(height):
                line: Line = cell.lines[line_idx] if line_idx < len(cell.lines) else Line([])
                buf.set_line(col.left, y + line_idx, line.patch_style(style), col.width)
