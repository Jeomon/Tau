from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent
from tau.tui.utils import BRIGHT_BLACK, RESET, visible_width

if TYPE_CHECKING:
    from tau.tui.theme import ColorFn


class Box(Component):
    """
    Padded container with an optional background ColorFn applied to every line.

    Usage::

        box = Box(my_component.render, padding_x=1, padding_y=0, bg_fn=theme.selected)
        lines = box.render(width)
    """

    def __init__(
        self,
        render_fn: Callable[[int], list[str]],
        padding_x: int = 0,
        padding_y: int = 0,
        bg_fn: ColorFn | None = None,
    ) -> None:
        self._render_fn = render_fn
        self._padding_x = max(0, padding_x)
        self._padding_y = max(0, padding_y)
        self._bg_fn = bg_fn
        self._cache: list[str] | None = None
        self._cache_width = 0

    # -------------------------------------------------------------------------
    # Public helpers
    # -------------------------------------------------------------------------

    def invalidate(self) -> None:
        self._cache = None

    def set_bg_fn(self, bg_fn: ColorFn | None) -> None:
        self._bg_fn = bg_fn
        self._cache = None

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        if self._cache is not None and self._cache_width == width:
            return self._cache
        self._cache = self._build(width)
        self._cache_width = width
        return self._cache

    def handle_input(self, event: InputEvent) -> bool:
        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _build(self, width: int) -> list[str]:
        inner_w = max(1, width - self._padding_x * 2)
        raw = self._render_fn(inner_w)
        pad_x = " " * self._padding_x

        def _apply(line: str) -> str:
            # Pad the visible content to `width` columns, then apply bg.
            vw = visible_width(line)
            fill = max(0, width - vw)
            full = line + " " * fill
            return self._bg_fn(full) if self._bg_fn else full

        out: list[str] = []
        blank = " " * width

        for _ in range(self._padding_y):
            out.append(_apply(blank) if self._bg_fn else blank)

        for line in raw:
            out.append(_apply(pad_x + line))

        for _ in range(self._padding_y):
            out.append(_apply(blank) if self._bg_fn else blank)

        return out


# ── DynamicBorder ─────────────────────────────────────────────────────────────


class DynamicBorder(Component):
    """Full-width horizontal rule that adapts to the terminal width."""

    def __init__(self, color: Callable[[str], str] | None = None) -> None:
        self._color = color or (lambda s: BRIGHT_BLACK + s + RESET)

    def render(self, width: int) -> list[str]:
        return [self._color("─" * max(1, width))]

    def invalidate(self) -> None:
        pass
