"""BarChart: vertical bars with labels, mirroring ratatui's ``widgets::BarChart``.

Height uses full eighth-block resolution (``symbols.FILL_VERTICAL``). Bars
can be flat (``bars=[...]``, unchanged from before) or clustered into
labeled ``BarGroup``s for side-by-side comparisons — passing ``groups``
adds a second label row underneath the per-bar labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.utils import truncate
from tau.tui.widgets.symbols import FILL_VERTICAL


@dataclass(slots=True)
class Bar:
    label: str
    value: float


@dataclass(slots=True)
class BarGroup:
    label: str
    bars: list[Bar]


@dataclass(slots=True)
class BarChart:
    bars: list[Bar] = field(default_factory=list)
    groups: list[BarGroup] | None = None
    bar_width: int = 3
    bar_gap: int = 1
    group_gap: int = 2
    style: Style = field(default_factory=Style)
    bar_style: Style = field(default_factory=Style)
    max_value: float | None = None

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        groups = self.groups if self.groups is not None else [BarGroup("", self.bars)]
        all_bars = [bar for group in groups for bar in group.bars]
        if not all_bars:
            return

        has_group_labels = self.groups is not None and any(g.label for g in groups)
        has_bar_labels = area.height > (2 if has_group_labels else 1)
        label_rows = (1 if has_bar_labels else 0) + (1 if has_group_labels else 0)
        chart_height = area.height - label_rows
        if chart_height <= 0:
            return

        peak = self.max_value
        if peak is None:
            peak = max((b.value for b in all_bars), default=0)
        peak = peak or 1
        levels = chart_height * (len(FILL_VERTICAL) - 1)
        bar_label_y = area.top + chart_height
        group_label_y = bar_label_y + (1 if has_bar_labels else 0)

        x = area.left
        for group in groups:
            group_start = x
            for bar in group.bars:
                if x >= area.right:
                    break
                width = min(self.bar_width, area.right - x)
                self._draw_bar(buf, x, area.top, chart_height, width, bar.value, peak, levels)
                if has_bar_labels:
                    self._draw_label(buf, x, bar_label_y, width, bar.label, self.style)
                x += width + self.bar_gap

            group_end = max(group_start, x - self.bar_gap)
            if has_group_labels and group.label:
                group_width = group_end - group_start
                self._draw_label(
                    buf, group_start, group_label_y, group_width, group.label, self.style
                )
            x += self.group_gap - self.bar_gap

    def _draw_bar(
        self,
        buf: Buffer,
        x: int,
        top: int,
        chart_height: int,
        width: int,
        value: float,
        peak: float,
        levels: int,
    ) -> None:
        eighths = round(max(0.0, min(1.0, value / peak)) * levels)
        full_rows, remainder = divmod(eighths, len(FILL_VERTICAL) - 1)
        for row in range(chart_height):
            from_bottom = chart_height - 1 - row
            if from_bottom < full_rows:
                glyph = FILL_VERTICAL[-1]
            elif from_bottom == full_rows and remainder:
                glyph = FILL_VERTICAL[remainder]
            else:
                glyph = " "
            buf.set_string(x, top + row, glyph * width, self.bar_style)

    def _draw_label(self, buf: Buffer, x: int, y: int, width: int, text: str, style: Style) -> None:
        if width <= 0:
            return
        label = truncate(text, width, ellipsis="")
        pad_left = max(0, (width - len(label)) // 2)
        buf.set_string(x, y, " " * pad_left + label, style, width)
