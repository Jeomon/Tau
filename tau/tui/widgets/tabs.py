"""Tabs: a horizontal strip of titles with one highlighted.

Supports styled tab titles, selection, dividers, and padding.

Produces a line: this is a single row of styled runs, and the renderer
consumes lines. ``render_line`` is the contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from tau.tui.layout import Alignment
from tau.tui.style import Style, apply_style
from tau.tui.text import Line
from tau.tui.utils import truncate_to_width, visible_width


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

    def render_line(self, width: int) -> str:
        """Return the tab strip as one styled ANSI line, clipped to ``width``."""
        if width <= 0 or not self.titles:
            return ""

        out: list[str] = []
        col = 0
        for i, title in enumerate(self.titles):
            if col >= width:
                break
            style = self.highlight_style if i == self.selected else self.style
            box_width = min(self.padding_left + title.width + self.padding_right, width - col)

            if self.padding_left:
                pad = truncate_to_width(" " * self.padding_left, box_width)
                out.append(apply_style(style, pad))
                col += visible_width(pad)

            title_width = max(0, box_width - self.padding_left - self.padding_right)
            patched = title.patch_style(style)
            # Mirrors Buffer.set_line: alignment resolved by padding, and the
            # line's base style merged behind each span's own.
            room = title_width
            if room > 0 and patched.alignment is not Alignment.LEFT:
                slack = max(0, room - patched.width)
                lead = slack // 2 if patched.alignment is Alignment.CENTER else slack
                if lead:
                    out.append(" " * lead)
                    room -= lead
                    col += lead
            for span in patched:
                if room <= 0:
                    break
                text = truncate_to_width(span.content, room)
                if not text:
                    continue
                out.append(apply_style(patched.style.patch(span.style), text))
                taken = visible_width(text)
                room -= taken
                col += taken

            # set_line reserves title_width regardless of how much the title
            # actually used; a clipped wide glyph leaves the remainder blank
            # rather than pulling the right padding leftwards.
            if room > 0:
                gap = min(room, width - col)
                if gap > 0:
                    out.append(" " * gap)
                    col += gap

            if self.padding_right:
                pad_cols = max(0, min(self.padding_right, width - col))
                if pad_cols:
                    out.append(apply_style(style, " " * pad_cols))
                    col += pad_cols

            if i < len(self.titles) - 1 and col < width:
                div = truncate_to_width(self.divider, width - col)
                if div:
                    out.append(apply_style(self.style, div))
                    col += visible_width(div)
        return "".join(out)
