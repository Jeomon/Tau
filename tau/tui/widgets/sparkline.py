"""Sparkline: a compact trend line, mirroring ratatui's ``widgets::Sparkline``.

Uses the same eighth-block vertical stacking as ``BarChart`` across the
full ``area.height`` (not just a single row), plus ``direction`` — which
end of ``data`` anchors to which side of the widget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.widgets.symbols import FILL_VERTICAL


class RenderDirection(Enum):
    LEFT_TO_RIGHT = auto()
    RIGHT_TO_LEFT = auto()


@dataclass(slots=True)
class Sparkline:
    data: list[float]
    style: Style = field(default_factory=Style)
    max_value: float | None = None
    direction: RenderDirection = RenderDirection.LEFT_TO_RIGHT

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty() or not self.data:
            return
        window = self.data[-area.width :]
        peak = self.max_value if self.max_value is not None else max(window, default=0)
        peak = peak or 1
        levels = area.height * (len(FILL_VERTICAL) - 1)
        n = len(window)
        left_to_right = self.direction is RenderDirection.LEFT_TO_RIGHT
        start_x = area.right - n if left_to_right else area.left

        for i, value in enumerate(window):
            col_offset = i if left_to_right else n - 1 - i
            x = start_x + col_offset
            eighths = round(max(0.0, min(1.0, value / peak)) * levels)
            full_rows, remainder = divmod(eighths, len(FILL_VERTICAL) - 1)
            for row in range(area.height):
                from_bottom = area.height - 1 - row
                if from_bottom < full_rows:
                    glyph = FILL_VERTICAL[-1]
                elif from_bottom == full_rows and remainder:
                    glyph = FILL_VERTICAL[remainder]
                else:
                    glyph = " "
                buf.set(x, area.top + row, glyph, self.style)
