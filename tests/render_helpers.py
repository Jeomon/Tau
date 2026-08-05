"""Shared test helper for rendering a Component.

Several test files need a component's ANSI-string output (or just to trigger
render's side effects, e.g. lazily starting a cursor-blink task) now that
``render(width) -> list[str]`` is the sole Component contract.

Rows are padded out to ``width`` with plain spaces. The renderer itself trims
trailing blanks, so this is not what reaches the terminal — but it is what the
previous scratch-Buffer helper produced (a buffer row is ``width`` cells wide
whether or not anything wrote to them), and column-alignment assertions are
written against it.
"""

from __future__ import annotations

from tau.tui.component import Component
from tau.tui.utils import visible_width


def render_to_lines(component: Component, width: int) -> list[str]:
    """Render ``component`` at ``width`` columns, padding each row out to ``width``."""
    return [line + " " * max(0, width - visible_width(line)) for line in component.render(width)]


#: Previous name, kept so the many call sites read consistently.
render_cells_to_lines = render_to_lines
