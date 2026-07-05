"""Shared test helper for rendering a Component via render_cells.

Several test files need a scratch-Buffer render to assert on ANSI-string
output (or just to trigger render_cells's side effects, e.g. lazily starting
a cursor-blink task) now that render_cells is the sole Component contract.
"""

from __future__ import annotations

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect


def render_cells_to_lines(component: Component, width: int) -> list[str]:
    """Render ``component`` into a scratch Buffer and flatten it to ANSI-string rows."""
    buf = Buffer.empty(Rect(0, 0, width, 0))
    rows = component.render_cells(Rect(0, 0, width, 0), buf)
    return [row_to_ansi(buf, y) for y in range(rows)]
