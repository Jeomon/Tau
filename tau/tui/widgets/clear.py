"""Clear: blank a Rect before an overlay draws over it, mirroring ratatui's ``widgets::Clear``.

Tau's overlay system (``TUI.set_widget`` in ``tui.py``) already clears space
for popovers ad hoc; this is the same operation as a reusable widget so any
new overlay/modal widget can call ``Clear().render(area, buf)`` instead of
hand-rolling a blank-fill loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from tau.tui.buffer import Buffer, Cell
from tau.tui.geometry import Rect


@dataclass(slots=True)
class Clear:
    def render(self, area: Rect, buf: Buffer) -> None:
        target = buf.area.intersection(area)
        for y in range(target.top, target.bottom):
            for x in range(target.left, target.right):
                buf.content[buf.index_of(x, y)] = Cell()
