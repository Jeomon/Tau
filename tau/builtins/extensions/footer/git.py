"""Git branch badge component."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.style import Style
from tau.tui.text import Line, Span

from .utils import read_branch, shorten_home

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect


class GitBadge(Component):
    """Renders ``~/path (branch)`` for the footer Row left slot."""

    def __init__(self) -> None:
        self._text = ""

    def update(self, cwd: str) -> None:
        branch = read_branch(cwd)
        display = shorten_home(cwd)
        self._text = f"{display} ({branch})" if branch else display

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(area.y + 1)
        buf.set_line(area.x, area.y, Line([Span(self._text, Style().dim())]), area.width)
        return 1

    def handle_input(self, event: object) -> bool:  # noqa: ARG002
        return False

    def invalidate(self) -> None:
        pass
