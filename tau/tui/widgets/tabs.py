"""Tabs: a horizontal strip of titles with one highlighted.

Mirrors ratatui's ``widgets::Tabs``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line


@dataclass(slots=True)
class Tabs:
    titles: list[Line]
    selected: int = 0
    style: Style = field(default_factory=Style)
    highlight_style: Style = field(default_factory=lambda: Style().bold())
    divider: str = " │ "
    padding_left: int = 0
    padding_right: int = 0

    def __init__(
        self,
        titles: Iterable[Line | str],
        selected: int = 0,
        style: Style | None = None,
        highlight_style: Style | None = None,
        divider: str = " │ ",
        padding_left: int = 0,
        padding_right: int = 0,
    ) -> None:
        self.titles = [Line.from_like(t) for t in titles]
        self.selected = selected
        self.style = style or Style()
        self.highlight_style = highlight_style or Style().bold()
        self.divider = divider
        self.padding_left = max(0, padding_left)
        self.padding_right = max(0, padding_right)

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty() or not self.titles:
            return
        x, end = area.left, area.right
        for i, title in enumerate(self.titles):
            if x >= end:
                break
            style = self.highlight_style if i == self.selected else self.style
            box_width = min(self.padding_left + title.width + self.padding_right, end - x)

            if self.padding_left:
                buf.set_string(x, area.top, " " * self.padding_left, style, box_width)
            title_x = x + min(self.padding_left, box_width)
            title_width = max(0, box_width - self.padding_left - self.padding_right)
            buf.set_line(title_x, area.top, title.patch_style(style), title_width)
            if self.padding_right:
                pad_x = title_x + title_width
                remaining = max(0, x + box_width - pad_x)
                buf.set_string(pad_x, area.top, " " * self.padding_right, style, remaining)

            x += box_width
            if i < len(self.titles) - 1 and x < end:
                x = buf.set_string(x, area.top, self.divider, self.style, end - x)
