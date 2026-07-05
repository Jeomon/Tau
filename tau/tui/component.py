from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.geometry import Rect

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.input import InputEvent


class Component(ABC):
    """
    Base class for all TUI components.

    ``render_cells(area, buf) -> int`` is the sole render contract: write
    directly into ``buf`` starting at row ``area.y`` and return the number of
    rows written. Every subclass must override it — a subclass that doesn't
    fails at construction time (``TypeError``) rather than at first render.
    """

    @abstractmethod
    def render_cells(self, area: Rect, buf: Buffer) -> int:
        """Render into ``buf`` starting at row ``area.y``; return rows written.

        ``buf`` starts at height 0 and grows on demand — an override must
        call ``buf.grow_to(area.y + n)`` before writing row
        ``area.y + n - 1``; ``Buffer.set``/``set_string`` silently no-op on
        an out-of-bounds row rather than growing it themselves (growing
        implicitly on every write would be surprising for the fixed-size
        buffers the ratatui-style widgets in ``tui/widgets/`` render into).
        """

    def handle_input(self, event: InputEvent) -> bool:  # noqa: ARG002
        """
        Handle a keyboard / mouse / paste event.

        Returns True if the event was consumed (stops propagation).
        Default: not handled.
        """
        return False

    def invalidate(self) -> None:  # noqa: B027
        """
        Clear any cached render state.

        Called by the renderer after a terminal resize or when the component
        needs to be fully re-rendered on the next frame.
        """

    def dispose(self) -> None:  # noqa: B027
        """Release background tasks or subscriptions owned by the component."""


class Focusable:
    """
    Mixin for components that want explicit keyboard focus.

    When TUI.set_focus(component) is called, TUI sets ``focused = True``
    on the component and routes handle_input() calls to it exclusively
    until focus changes.  Components that display a text cursor or need
    IME positioning should implement this interface.

    Example::

        class MyInput(Component, Focusable):
            def render_cells(self, area, buf):
                cursor = "█" if self.focused else ""
                buf.grow_to(area.y + 1)
                buf.set_string(area.x, area.y, f"> {self._text}{cursor}")
                return 1
    """

    focused: bool = False


class Container(Component):
    """
    An ordered list of child components rendered top-to-bottom.

    An ordered list of child components rendered top-to-bottom.
    Children are rendered in insertion order; each child gets the full
    available width.

    Usage::

        header = Container()
        header.add_child(Banner())
        header.add_child(Spacer(1))

        tui.add_child(header)
        tui.add_child(chat)
        tui.add_child(editor)
    """

    def __init__(self) -> None:
        self.children: list[Component] = []

    def add_child(self, component: Component) -> None:
        """Append a component to the bottom of this container."""
        self.children.append(component)

    def remove_child(self, component: Component) -> None:
        """Remove a component; no-op if not present."""
        with contextlib.suppress(ValueError):
            self.children.remove(component)

    def clear(self) -> None:
        """Remove all children."""
        self.children.clear()

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        y = area.y
        for child in self.children:
            y += child.render_cells(Rect(area.x, y, area.width, 0), buf)
        return y - area.y

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child in self.children)

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def dispose(self) -> None:
        for child in self.children:
            child.dispose()


class StaticComponent(Component):
    """
    A component backed by a fixed list of pre-rendered lines.
    Useful for testing and simple static content.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        row = 0
        for line in self._lines:
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class Text(Component):
    """Mutable width-aware text component.

    Hard newlines are preserved and long lines wrap to the available terminal
    width. An optional style function can apply ANSI formatting.
    """

    def __init__(
        self,
        text: str = "",
        style: Callable[[str], str] | None = None,
    ) -> None:
        self._text = text
        self._style = style

    @property
    def text(self) -> str:
        """Return the current text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Replace the rendered text."""
        self._text = text

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        content = self._style(self._text) if self._style is not None else self._text
        row = 0
        for line in content.split("\n"):
            row += parse_ansi_wrapped_into(buf, area.x, area.y + row, line, area.width)
        return row


class Column(Component):
    """
    Renders children top-to-bottom, each getting the full width.

    Fixed counterpart to ``Container`` — children are supplied at construction
    time.  Use ``Container`` when you need to add/remove children at runtime.

    Usage::

        col = Column([Banner(), Divider(), ChatArea()])
    """

    def __init__(self, children: list[Component]) -> None:
        self.children = list(children)

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        y = area.y
        for child in self.children:
            y += child.render_cells(Rect(area.x, y, area.width, 0), buf)
        return y - area.y

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child in reversed(self.children))

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def dispose(self) -> None:
        for child in self.children:
            child.dispose()


# Backwards-compatible alias
VerticalStack = Column


class Row(Component):
    """
    Renders children side-by-side in a single terminal line.

    Each child is assigned a slot — ``"left"``, ``"center"``, or ``"right"``
    — and the Row distributes the available width so that:

    - left content is flush-left
    - right content is flush-right
    - center content sits in the middle (best-effort)

    Each child is rendered at its measured slot width and only its first row
    is used.  This keeps Row a single-line primitive; stack multiple Rows
    inside a Column/Container for multi-line horizontal layouts.

    Usage::

        row = Row([
            (GitBadge(),   "left"),
            (StatusBadge(),"center"),
            (ModelBadge(), "right"),
        ])
    """

    def __init__(self, slots: list[tuple[Component, str]] | None = None) -> None:
        self._slots: list[tuple[Component, str]] = list(slots) if slots else []

    def add_slot(self, component: Component, align: str = "left") -> None:
        """Append a component with the given alignment (``"left"``, ``"center"``, ``"right"``)."""
        self._slots.append((component, align))

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.buffer import Buffer
        from tau.tui.style import Style

        groups: dict[str, list[tuple[Buffer, int]]] = {
            "left": [],
            "center": [],
            "right": [],
        }
        for component, align in self._slots:
            child = Buffer.empty(Rect(0, 0, area.width, 0))
            rows = component.render_cells(Rect(0, 0, area.width, 0), child)
            content_width = 0
            if rows:
                for column in range(area.width):
                    cell = child.get(column, 0)
                    if cell.symbol != " " or cell.style != Style() or cell.skip:
                        content_width = column + 1
            groups[align if align in groups else "left"].append((child, content_width))

        def group_width(group: list[tuple[Buffer, int]]) -> int:
            return sum(width for _, width in group) + 2 * max(0, len(group) - 1)

        left_width = group_width(groups["left"])
        center_width = group_width(groups["center"])
        right_width = group_width(groups["right"])
        starts = {
            "left": 0,
            "center": max(left_width + 1, (area.width - center_width) // 2),
            "right": max(0, area.width - right_width),
        }
        if groups["center"] and starts["center"] + center_width > starts["right"]:
            starts["center"] = max(left_width + 1, starts["right"] - center_width - 1)

        buf.grow_to(area.y + 1)
        for align in ("left", "center", "right"):
            column = starts[align]
            for index, (child, width) in enumerate(groups[align]):
                if index:
                    column += 2
                if width:
                    buf.blit(child, area.x + column, area.y, Rect(0, 0, width, 1))
                column += width
        return 1

    def handle_input(self, event: InputEvent) -> bool:
        return any(component.handle_input(event) for component, _ in self._slots)

    def invalidate(self) -> None:
        for component, _ in self._slots:
            component.invalidate()


def _resolve_width(spec: int | str, available: int) -> int:
    """Resolve an absolute or ``"NN%"`` width spec against the available columns.

    The result is clamped to ``[0, available]``.
    """
    if isinstance(spec, str) and spec.strip().endswith("%"):
        try:
            pct = float(spec.strip()[:-1])
        except ValueError:
            return available
        value = int(available * pct / 100)
    else:
        try:
            value = int(spec)
        except (TypeError, ValueError):
            return available
    return max(0, min(value, available))


class Constrained(Component):
    """
    Render a child at a fixed width, then place that block within the full width.

    ``width`` is an absolute column count (``40``) or a percentage of the
    available width (``"30%"``). The child is rendered at that target width and
    every line is padded/truncated to it, producing a solid rectangle which is
    then aligned ``"left"``, ``"center"``, or ``"right"`` within the parent.

    Use this to give an in-flow widget (e.g. ``set_widget``) a fixed width
    instead of the full terminal width.

    Usage::

        # a 40-column panel pinned to the right edge
        Constrained(StatusPanel(), width=40, align="right")
        # a sidebar taking 30% of the width
        Constrained(Sidebar(), width="30%")
    """

    def __init__(
        self,
        child: Component,
        width: int | str,
        align: str = "left",
    ) -> None:
        self._child = child
        self._width = width
        self._align = align

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.buffer import Buffer

        target = max(1, _resolve_width(self._width, area.width))
        child = Buffer.empty(Rect(0, 0, target, 0))
        rows = self._child.render_cells(Rect(0, 0, target, 0), child)
        offset = 0
        if self._align == "center":
            offset = max(0, (area.width - target) // 2)
        elif self._align == "right":
            offset = max(0, area.width - target)
        buf.grow_to(area.y + rows)
        buf.blit(child, area.x + offset, area.y)
        return rows

    def handle_input(self, event: InputEvent) -> bool:
        return self._child.handle_input(event)

    def invalidate(self) -> None:
        self._child.invalidate()


class Columns(Component):
    """
    Render children side by side as fixed-width columns, merged line by line.

    Each entry is ``(child, width)`` where ``width`` is an absolute column
    count, a percentage string (``"30%"``), or ``None`` for a flexible column
    that splits the leftover width evenly with the other flex columns. ``gap``
    spaces separate the columns.

    Unlike ``Row`` (single line, alignment based), ``Columns`` preserves each
    child's full multi-line output and pads every column to its width, so
    borders and backgrounds line up. Short columns are padded with blank lines
    to match the tallest.

    Usage::

        Columns([(Sidebar(), 30), (Chat(), None)], gap=2)
        Columns([(Left(), "50%"), (Right(), "50%")])
    """

    def __init__(
        self,
        slots: list[tuple[Component, int | str | None]] | None = None,
        gap: int = 1,
    ) -> None:
        self._slots: list[tuple[Component, int | str | None]] = list(slots) if slots else []
        self._gap = max(0, gap)

    def _column_widths(self, available: int) -> list[int]:
        """Resolve each slot to a concrete column width (flex slots share remainder)."""
        gaps = self._gap * max(0, len(self._slots) - 1)
        usable = max(0, available - gaps)
        widths: list[int] = [0] * len(self._slots)
        flex: list[int] = []
        used = 0
        for i, (_, spec) in enumerate(self._slots):
            if spec is None:
                flex.append(i)
                continue
            cw = _resolve_width(spec, usable)
            widths[i] = cw
            used += cw
        leftover = max(0, usable - used)
        if flex:
            share = leftover // len(flex)
            rem = leftover - share * len(flex)
            for j, i in enumerate(flex):
                widths[i] = share + (1 if j < rem else 0)
        return widths

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.buffer import Buffer

        widths = self._column_widths(area.width)
        rendered: list[tuple[Buffer, int]] = []
        height = 0
        for (child, _), width in zip(self._slots, widths, strict=True):
            child_buf = Buffer.empty(Rect(0, 0, width, 0))
            rows = child.render_cells(Rect(0, 0, width, 0), child_buf) if width > 0 else 0
            rendered.append((child_buf, rows))
            height = max(height, rows)
        buf.grow_to(area.y + height)
        x = area.x
        for (child_buf, _), width in zip(rendered, widths, strict=True):
            if width > 0:
                buf.blit(child_buf, x, area.y)
                x += width + self._gap
        return height

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child, _ in self._slots)

    def invalidate(self) -> None:
        for child, _ in self._slots:
            child.invalidate()


class Rows(Component):
    """
    Stack children vertically with fixed / percent / flex heights.

    Vertical dual of ``Columns``. Each entry is ``(child, height)`` where height
    is an absolute line count, a percentage string (``"30%"``), or ``None`` for
    a flexible row that splits the leftover height evenly. ``gap`` blank lines
    separate rows. Each child is padded (with blank lines) or truncated to its
    row height so the total layout is predictable.

    Because ``render_cells`` only receives the available *width* via ``area``,
    the total height budget must be supplied explicitly via ``height`` — e.g. an overlay's
    ``max_height`` or a fixed dashboard region. When ``height`` is ``None``,
    percent/flex rows fall back to their natural content height and only
    absolute rows are constrained, so it behaves like a height-capped
    ``Column``.

    Usage::

        # a 30-line panel: 1-line header, flexible body, 1-line footer
        Rows([(Header(), 1), (Body(), None), (Footer(), 1)], height=30)
        Rows([(Top(), "50%"), (Bottom(), "50%")], height=20)
    """

    def __init__(
        self,
        slots: list[tuple[Component, int | str | None]] | None = None,
        height: int | None = None,
        gap: int = 0,
    ) -> None:
        self._slots: list[tuple[Component, int | str | None]] = list(slots) if slots else []
        self._height = height
        self._gap = max(0, gap)

    def _row_heights(self, natural: list[int]) -> list[int]:
        """Resolve each slot to a concrete line count.

        ``natural`` is each child's rendered height, used for flex/percent rows
        when no explicit ``height`` budget is set.
        """
        if self._height is None:
            heights: list[int] = []
            for (_, spec), nat in zip(self._slots, natural, strict=True):
                if spec is None or (isinstance(spec, str) and spec.strip().endswith("%")):
                    # No budget to resolve flex/percent against — keep natural.
                    heights.append(nat)
                else:
                    try:
                        heights.append(max(0, int(spec)))
                    except (TypeError, ValueError):
                        heights.append(nat)
            return heights

        gaps = self._gap * max(0, len(self._slots) - 1)
        usable = max(0, self._height - gaps)
        heights = [0] * len(self._slots)
        flex: list[int] = []
        used = 0
        for i, (_, spec) in enumerate(self._slots):
            if spec is None:
                flex.append(i)
                continue
            rh = _resolve_width(spec, usable)
            heights[i] = rh
            used += rh
        leftover = max(0, usable - used)
        if flex:
            share = leftover // len(flex)
            rem = leftover - share * len(flex)
            for j, i in enumerate(flex):
                heights[i] = share + (1 if j < rem else 0)
        return heights

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        from tau.tui.buffer import Buffer

        children: list[tuple[Buffer, int]] = []
        for child, _ in self._slots:
            child_buf = Buffer.empty(Rect(0, 0, area.width, 0))
            rows = child.render_cells(Rect(0, 0, area.width, 0), child_buf)
            children.append((child_buf, rows))
        heights = self._row_heights([rows for _, rows in children])
        y = area.y
        for index, ((child_buf, rows), height) in enumerate(zip(children, heights, strict=True)):
            if index and self._gap:
                y += self._gap
            if height <= 0:
                continue
            buf.blit(
                child_buf,
                area.x,
                y,
                Rect(0, 0, area.width, min(rows, height)),
            )
            y += height
        buf.grow_to(y)
        return y - area.y

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child, _ in self._slots)

    def invalidate(self) -> None:
        for child, _ in self._slots:
            child.invalidate()
