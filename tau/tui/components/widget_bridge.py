"""Bridge a ratatui-style Widget into the Component tree.

A ``Widget`` (``tau/tui/widget.py``) paints into a ``Rect`` of a shared
``Buffer`` directly, with no owned return value — this wraps one as a
``Component`` so it composes with ``Container``/``Column``/``Row`` the same
way any other component does. ``render_widget_lines`` additionally exposes
a plain ANSI-string view of a widget's output, for callers that want a
``list[str]`` rather than writing into a caller-supplied ``Buffer``.
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
    """Wrap a ``Widget`` (or a ``width -> Widget`` factory) as a ``Component``.

    ``height`` is fixed since a ``Buffer`` needs a concrete area; compose
    with ``Column`` or ``Rows`` for multi-part layouts.
    """

    def __init__(self, widget: Widget | Callable[[int], Widget], height: int = 1) -> None:
        self._widget = widget
        self._height = max(1, height)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        widget = self._widget(area.width) if callable(self._widget) else self._widget
        buf.grow_to(area.y + self._height)
        widget.render(Rect(area.x, area.y, area.width, self._height), buf)
        return self._height

    def handle_input(self, event: InputEvent) -> bool:  # noqa: ARG002
        return False
