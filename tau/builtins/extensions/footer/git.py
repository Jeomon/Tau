"""Git branch badge component."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.compose import line_to_ansi
from tau.tui.style import Style
from tau.tui.text import Line, Span

from .utils import read_branch, shorten_home

if TYPE_CHECKING:
    pass


class GitBadge(Component):
    """Renders ``~/path (branch)`` for the footer Row left slot."""

    def __init__(self) -> None:
        self._text = ""

    def update(self, cwd: str) -> bool:
        """Re-read the branch; return True if the displayed text changed."""
        branch = read_branch(cwd)
        display = shorten_home(cwd)
        text = f"{display} ({branch})" if branch else display
        changed = text != self._text
        self._text = text
        return changed

    def render(self, width: int) -> list[str]:
        return [line_to_ansi(Line([Span(self._text, Style().dim())]), width)]

    def handle_input(self, event: object) -> bool:  # noqa: ARG002
        return False

    def invalidate(self) -> None:
        pass
