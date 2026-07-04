from __future__ import annotations

from collections.abc import Callable

from tau.tui.ansi_bridge import parse_ansi_into
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

        box = Box(my_component.render, padding_x=1, padding_y=0, bg_style=theme.selected)
        lines = box.render(width)
    """

    def __init__(
        self,
        render_fn: Callable[[int], list[str]],
        padding_x: int = 0,
        padding_y: int = 0,
        bg_style: Style | None = None,
    ) -> None:
        self._render_fn = render_fn
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

    def handle_input(self, event: InputEvent) -> bool:  # noqa: ARG002
        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _build(self, width: int) -> Buffer:
        inner_w = max(1, width - self._padding_x * 2)
        raw = self._render_fn(inner_w)
        total_rows = self._padding_y * 2 + len(raw)

        buf = Buffer.empty(Rect(0, 0, width, total_rows))

        y = self._padding_y
        for line in raw:
            parse_ansi_into(buf, self._padding_x, y, line, width - self._padding_x)
            y += 1

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

    Renders via the ratatui-style ``Block`` widget directly (a Buffer with
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
