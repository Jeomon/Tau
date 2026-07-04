"""Gauge/LineGauge: progress indicators, mirroring ratatui's ``widgets::Gauge``/``LineGauge``.

``Gauge`` fills a full-height bar one eighth-block at a time and centers a
percentage label over it. ``LineGauge`` is the compact single-row form
(label, then a thin fill line) used inline in status bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.widgets.symbols import FILL_HORIZONTAL


def _fill_row(
    buf: Buffer, x: int, y: int, width: int, ratio: float, style: Style, gauge_style: Style
) -> None:
    ratio = max(0.0, min(1.0, ratio))
    filled_eighths = round(width * 8 * ratio)
    full_cells, remainder = divmod(filled_eighths, 8)
    for i in range(width):
        if i < full_cells:
            buf.set(x + i, y, FILL_HORIZONTAL[-1], gauge_style)
        elif i == full_cells and remainder:
            buf.set(x + i, y, FILL_HORIZONTAL[remainder], gauge_style)
        else:
            buf.set(x + i, y, " ", style)


@dataclass(slots=True)
class Gauge:
    ratio: float = 0.0
    label: str | None = None
    style: Style = field(default_factory=Style)
    gauge_style: Style = field(default_factory=lambda: Style().reversed())

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        mid_y = area.top + area.height // 2
        for y in range(area.top, area.bottom):
            _fill_row(buf, area.left, y, area.width, self.ratio, self.style, self.gauge_style)

        text = self.label if self.label is not None else f"{round(self.ratio * 100)}%"
        label_x = area.left + max(0, (area.width - len(text)) // 2)
        buf.set_string(label_x, mid_y, text, self.style, area.width)


@dataclass(slots=True)
class LineGauge:
    ratio: float = 0.0
    label: str = ""
    style: Style = field(default_factory=Style)
    gauge_style: Style = field(default_factory=lambda: Style().bold())

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        label_width = len(self.label) + 1 if self.label else 0
        if self.label:
            buf.set_string(area.left, area.top, self.label + " ", self.style, label_width)
        bar_width = max(0, area.width - label_width)
        bar_x = area.left + label_width
        _fill_row(buf, bar_x, area.top, bar_width, self.ratio, self.style, self.gauge_style)
