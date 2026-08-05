from __future__ import annotations

from tau.tui.ansi_bridge import row_to_ansi
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
        self._cache_lines: list[str] | None = None
        self._cache_width = 0

    # -------------------------------------------------------------------------
    # Public helpers
    # -------------------------------------------------------------------------

    def invalidate(self) -> None:
        self._cache_lines = None
        self._child.invalidate()

    def set_bg_style(self, bg_style: Style | None) -> None:
        self._bg_style = bg_style
        self._cache_lines = None

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        """Return the padded, background-filled child as lines.

        The composition itself (child + padding + a background patched *behind*
        the child's own styles) is genuinely grid work, so it is still built in
        a local Buffer — but only once per width, and it is flattened to lines
        there rather than copied cell by cell into the frame on every render.
        That copy was the whole cost here: width x rows Buffer.set calls per
        frame for content that had not changed.
        """
        if self._cache_lines is None or self._cache_width != width:
            built = self._build(width)
            self._cache_lines = [
                row_to_ansi(built, y, embed_raw=True) for y in range(built.area.height)
            ]
            self._cache_width = width
        return list(self._cache_lines)

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

    A Block with only the top border enabled draws exactly this rule, so it
    is built from ``Block.render_lines`` rather than a hand-assembled string —
    one definition of what the rule looks like, including its border set.
    """

    def __init__(self, style: Style | None = None) -> None:
        # Matches the old default ColorFn: BRIGHT_BLACK + s + RESET.
        self._style = style if style is not None else Style(fg="bright_black")

    def render(self, width: int) -> list[str]:
        block = Block(borders=Borders.TOP, border_style=self._style)
        return block.render_lines(max(1, width), 1)

    def invalidate(self) -> None:
        pass
