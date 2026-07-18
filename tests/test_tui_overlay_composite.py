"""Regression: overlay compositing must not mutate cells shared with history.

Renderer._composite_overlays used to write overlay pixels via Buffer.set,
which mutates non-sentinel Cell objects in place — but frozen-history rows in
the frame buffer hold the *same* Cell objects (by reference) as MessageList's
frozen buffer and TUI's widened-row cache (see TUI._splice_frozen_rows), so
opening any centered overlay over visible history permanently baked its
pixels into the frozen cache: ghost content persisted after the overlay
closed.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer, Cell
from tau.tui.geometry import Rect
from tau.tui.service import Renderer
from tau.tui.style import Style


class FakeTerminal:
    def __init__(self, width: int = 20, height: int = 5) -> None:
        self.width = width
        self.height = height

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        pass

    def write_flush(self, data: str) -> None:
        pass

    def on_resize(self, callback: object) -> object:
        return lambda: None


class _OverlayComponent:
    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(1)
        buf.set_string(0, 0, "XX")
        return 1


class _Entry:
    component = _OverlayComponent()

    def is_visible(self, width: int, height: int) -> bool:
        return True

    def resolve_width(self, width: int) -> int:
        return 2

    def resolve(self, width: int, height: int, natural_h: int) -> tuple[int, int, int, int]:
        # (width, height, row, col)
        return 2, natural_h, 0, 0


def test_composite_replaces_cells_instead_of_mutating_shared_history() -> None:
    term = FakeTerminal()
    renderer = Renderer(term)  # type: ignore[arg-type]

    frame = Buffer.empty(Rect(0, 0, term.width, term.height))
    shared = Cell("h", Style())
    # Frozen-history splicing shares Cell objects by reference between the
    # frame buffer and the persistent widened-row cache — model that here.
    cache_row = [shared]
    frame.content[frame.index_of(1, 0)] = shared

    renderer._composite_overlays(frame, [_Entry()], term.width, term.height)

    # The overlay pixel must land in the frame buffer...
    assert frame.get(1, 0).symbol == "X"
    assert frame.get(2, 0).symbol == "X"
    # ...but the cell shared with the cache must be untouched.
    assert cache_row[0] is shared
    assert shared.symbol == "h"
