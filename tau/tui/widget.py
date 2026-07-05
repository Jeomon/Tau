"""Widget / StatefulWidget: the render contract.

Rust's ``Widget::render(self, area, buf)`` consumes ``self`` because a widget
is a one-shot draw *command*, not a thing you hold onto. Python has no
ownership to enforce that, and Tau's components are already long-lived
mutable objects (``SelectList`` keeps its own selection index, ``TextInput``
its own cursor) — a retained ``StatefulWidget`` split rather than
its default immediate-mode ``Widget``. These protocols name both shapes
structurally (``Protocol``, so nothing has to inherit from them) rather than
picking one and forcing a rewrite.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect


@runtime_checkable
class Widget(Protocol):
    """A UI element that paints itself into a ``Rect`` of a ``Buffer``.

    Structurally close to ``Component.render_cells(area, buf)``, but writes
    into a caller-supplied ``Buffer`` with no owned return value — composition
    happens by writing into non-overlapping ``Rect``s of the same ``Buffer``,
    used by the grid widgets in ``tui/widgets/`` rather than the
    ``Component`` tree directly (see ``widget_bridge.py`` for the adapter
    between the two).
    """

    def render(self, area: Rect, buf: Buffer) -> None: ...


@runtime_checkable
class StatefulWidget(Protocol):
    """A ``Widget`` whose render also reads/writes external state (scroll offset, selection, ...).

    ``state`` is intentionally untyped here (``Any``); a concrete widget
    narrows it, e.g. ``SelectListState`` with a ``selected: int`` field.
    """

    def render(self, area: Rect, buf: Buffer, state: Any) -> None: ...


def render_widget(widget: Widget, area: Rect, buf: Buffer) -> None:
    """Render a widget into a frame buffer."""
    widget.render(area, buf)
