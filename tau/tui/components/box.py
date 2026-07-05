from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent
from tau.tui.style import Style
from tau.tui.widgets.block import Block, Borders


class Box(Component):
    """
    Padded container with an optional background Style applied to every line.

    Usage::

        box = Box(my_component, padding_x=1, padding_y=0, bg_style=theme.selected)
    """

    def __init__(
        self,
        child: Component,
        padding_x: int = 0,
        padding_y: int = 0,
        bg_style: Style | None = None,
    ) -> None:
        self._child = child
        self._padding_x = max(0, padding_x)
        self._padding_y = max(0, padding_y)
        self._bg_style = bg_style
        self._cache: Buffer | None = None
        self._cache_width = 0

    # -------------------------------------------------------------------------
    # Public helpers
    # -------------------------------------------------------------------------

    def invalidate(self) -> None:
        self._cache = None
        self._child.invalidate()

    def set_bg_style(self, bg_style: Style | None) -> None:
        self._bg_style = bg_style
        self._cache = None

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if self._cache is None or self._cache_width != area.width:
            self._cache = self._build(area.width)
            self._cache_width = area.width
        cached = self._cache

        rows = cached.area.height
        buf.grow_to(area.y + rows)
        for y in range(rows):
            for x in range(area.width):
                cell = cached.get(x, y)
                buf.set(area.x + x, area.y + y, cell.symbol, cell.style)
        return rows

    def handle_input(self, event: InputEvent) -> bool:
        return self._child.handle_input(event)

    def dispose(self) -> None:
        self._child.dispose()

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _build(self, width: int) -> Buffer:
        inner_w = max(1, width - self._padding_x * 2)
        inner = Buffer.empty(Rect(0, 0, inner_w, 0))
        inner_rows = self._child.render_cells(Rect(0, 0, inner_w, 0), inner)
        buf = Buffer.empty(Rect(0, 0, width, 0))
        buf.grow_to(self._padding_y + inner_rows + self._padding_y)
        buf.blit(
            inner,
            self._padding_x,
            self._padding_y,
            Rect(0, 0, inner_w, inner_rows),
        )

        # Apply after content so Style.patch merges the background behind
        # whatever fg/modifiers the content itself set, instead of a plain
        # overwrite clobbering them (matches the old ColorFn wrap, which
        # layered bg onto already-styled content via cumulative SGR codes).
        if self._bg_style is not None:
            buf.set_style(buf.area, self._bg_style)
        return buf


# ── DynamicBorder ─────────────────────────────────────────────────────────────


class DynamicBorder(Component):
    """Full-width horizontal rule that adapts to the terminal width.

    Renders via the grid-based ``Block`` widget directly (a Buffer with
    only the top border enabled draws exactly this rule) — Buffer-native,
    no ANSI round-trip.
    """

    def __init__(self, style: Style | None = None) -> None:
        # Matches the old default ColorFn: BRIGHT_BLACK + s + RESET.
        self._style = style if style is not None else Style(fg="bright_black")

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(area.y + 1)
        row = Rect(area.x, area.y, max(1, area.width), 1)
        Block(borders=Borders.TOP, border_style=self._style).render(row, buf)
        return 1

    def invalidate(self) -> None:
        pass
