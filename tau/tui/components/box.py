from __future__ import annotations

from tau.tui.ansi_text import patch_row_style
from tau.tui.component import Component
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

        Cached per width: the child's own render is the expensive part, and
        the padding and background around it do not change until the width or
        the style does.
        """
        if self._cache_lines is None or self._cache_width != width:
            self._cache_lines = self._build(width)
            self._cache_width = width
        return list(self._cache_lines)

    def handle_input(self, event: InputEvent) -> bool:
        return self._child.handle_input(event)

    def dispose(self) -> None:
        self._child.dispose()

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _build(self, width: int) -> list[str]:
        inner_w = max(1, width - self._padding_x * 2)
        pad = " " * self._padding_x
        blank = [""] * self._padding_y

        rows = [
            *blank,
            *(pad + line for line in self._child.render(inner_w)),
            *blank,
        ]

        # Patched behind the content so the background merges with whatever
        # fg/modifiers the content itself set, instead of a plain overwrite
        # clobbering them (matches the old ColorFn wrap, which layered bg onto
        # already-styled content via cumulative SGR codes). Padding columns are
        # filled too, so the box reads as a solid block.
        if self._bg_style is not None:
            rows = [patch_row_style(row, width, self._bg_style) for row in rows]
        return rows


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
