from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.input import InputEvent


class Component(ABC):
    """
    Base class for all TUI components.

    A component produces a list of pre-rendered lines for a given terminal
    width. The renderer calls render() on each frame and only writes lines
    that changed since the last frame.
    """

    @abstractmethod
    def render(self, width: int) -> list[str]:
        """
        Return the component's visual representation as a list of strings.

        Each string is one terminal line. Lines must not contain newline
        characters — the renderer handles line breaks. ANSI escape codes
        are allowed; the renderer uses ansi.visible_width() for comparisons.

        Args:
            width: Available terminal columns.

        Returns:
            List of strings, one per line.
        """
        ...

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


class Focusable:
    """
    Mixin for components that want explicit keyboard focus.

    When TUI.set_focus(component) is called, TUI sets ``focused = True``
    on the component and routes handle_input() calls to it exclusively
    until focus changes.  Components that display a text cursor or need
    IME positioning should implement this interface.

    Example::

        class MyInput(Component, Focusable):
            def render(self, width):
                cursor = "█" if self.focused else ""
                return [f"> {self._text}{cursor}"]
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

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child in self.children)

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()


class StaticComponent(Component):
    """
    A component backed by a fixed list of pre-rendered lines.
    Useful for testing and simple static content.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return self._lines


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

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child in reversed(self.children))

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()


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

    Each child's ``render()`` is called with its measured slot width and only
    the **first line** of the result is used.  This keeps Row a single-line
    primitive; stack multiple Rows inside a Column/Container for multi-line
    horizontal layouts.

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

    def render(self, width: int) -> list[str]:
        from tau.tui.ansi import truncate, visible_width

        left_parts: list[str] = []
        center_parts: list[str] = []
        right_parts: list[str] = []

        for component, align in self._slots:
            lines = component.render(width)
            text = lines[0] if lines else ""
            if align == "right":
                right_parts.append(text)
            elif align == "center":
                center_parts.append(text)
            else:
                left_parts.append(text)

        left = "  ".join(left_parts)
        center = "  ".join(center_parts)
        right = "  ".join(right_parts)

        lw = visible_width(left)
        cw = visible_width(center)
        rw = visible_width(right)

        if center:
            # left | center (centered) | right
            center_start = max(lw + 1, (width - cw) // 2)
            right_start = width - rw
            if center_start + cw > right_start:
                center_start = max(lw + 1, right_start - cw - 1)
            line = left
            line += " " * max(0, center_start - lw)
            line += center
            cur = center_start + cw
            line += " " * max(0, right_start - cur)
            line += right
        else:
            # left | right
            gap = width - lw - rw
            if gap >= 0:
                line = left + " " * gap + right
            else:
                line = truncate(left, max(0, width - rw)) + right

        return [line]

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

    def render(self, width: int) -> list[str]:
        from tau.tui.ansi import pad, truncate, visible_width

        target = max(1, _resolve_width(self._width, width))
        raw = self._child.render(target)
        out: list[str] = []
        for line in raw:
            fitted = truncate(line, target) if visible_width(line) > target else line
            # Pad the content to a solid `target`-wide rectangle, then place the
            # rectangle within the full parent width using the same alignment.
            block = pad(fitted, target, align=self._align)
            out.append(pad(block, width, align=self._align))
        return out

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

    def render(self, width: int) -> list[str]:
        from tau.tui.ansi import pad, truncate, visible_width

        widths = self._column_widths(width)
        columns: list[list[str]] = []
        height = 0
        for (child, _), cw in zip(self._slots, widths, strict=True):
            if cw <= 0:
                columns.append([])
                continue
            col: list[str] = []
            for line in child.render(cw):
                fitted = truncate(line, cw) if visible_width(line) > cw else line
                col.append(pad(fitted, cw))
            columns.append(col)
            height = max(height, len(col))

        gap = " " * self._gap
        out: list[str] = []
        for r in range(height):
            parts: list[str] = []
            for col, cw in zip(columns, widths, strict=True):
                if cw <= 0:
                    continue
                parts.append(col[r] if r < len(col) else " " * cw)
            line = gap.join(parts)
            if visible_width(line) > width:
                line = truncate(line, width)
            out.append(line)
        return out

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

    Because ``render()`` only receives the available *width*, the total height
    budget must be supplied explicitly via ``height`` — e.g. an overlay's
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

    def render(self, width: int) -> list[str]:
        rendered = [child.render(width) for child, _ in self._slots]
        heights = self._row_heights([len(r) for r in rendered])

        out: list[str] = []
        for idx, (lines, rh) in enumerate(zip(rendered, heights, strict=True)):
            if idx > 0 and self._gap:
                out.extend([""] * self._gap)
            if rh <= 0:
                continue
            block = lines[:rh] if len(lines) > rh else lines + [""] * (rh - len(lines))
            out.extend(block)
        return out

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child, _ in self._slots)

    def invalidate(self) -> None:
        for child, _ in self._slots:
            child.invalidate()
