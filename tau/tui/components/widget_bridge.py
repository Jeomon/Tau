"""Bridge a ratatui-style Widget into the legacy Component tree.

``tui.py``'s ``Renderer`` (and every ``Container``/``Column``/``Row`` in
``component.py``) only knows ``Component.render(width) -> list[str]`` — ANSI
strings, one per line. This renders a ``Widget`` into a real ``Buffer`` and
converts each row back into an ANSI string, so new Buffer/Widget-based code
can be dropped into the existing live tree without changing the renderer,
the diffing, or anything else in ``tui.py``.

One-directional on purpose: legacy ``Component``s are not converted the
other way (into ``Widget``s), since that would need parsing ANSI strings
back into styled cells — the exact problem ``tui.py``'s ``_parse_cells``
already exists to solve for diffing, not something to invoke a second time
here for content that's already plain Python objects on the new side.
"""

from __future__ import annotations

from collections.abc import Callable

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent
from tau.tui.widget import Widget


def render_widget_lines(widget: Widget, width: int, height: int) -> list[str]:
    """Render ``widget`` into a fresh ``Buffer`` and return it as ANSI-string lines."""
    area = Rect(0, 0, max(0, width), max(0, height))
    buf = Buffer.empty(area)
    widget.render(area, buf)
    return [row_to_ansi(buf, y) for y in range(area.top, area.bottom)]


class WidgetComponent(Component):
    """Wraps a ``Widget`` (or a ``width -> Widget`` factory) as a legacy ``Component``.

    ``height`` is fixed since a ``Buffer`` needs a concrete area; compose
    with the existing ``Column``/``Rows`` on the legacy side for multi-part
    layouts, same as any other ``Component``.
    """

    def __init__(self, widget: Widget | Callable[[int], Widget], height: int = 1) -> None:
        self._widget = widget
        self._height = max(1, height)

    def render(self, width: int) -> list[str]:
        widget = self._widget(width) if callable(self._widget) else self._widget
        return render_widget_lines(widget, width, self._height)

    def handle_input(self, event: InputEvent) -> bool:  # noqa: ARG002
        return False
