from __future__ import annotations

import contextlib
from abc import ABC
from collections.abc import Callable
from typing import TYPE_CHECKING

from tau.tui.geometry import Position

if TYPE_CHECKING:
    from tau.tui.input import InputEvent


def _child_lines(child: object, width: int) -> list[str]:
    """Ask a child for its lines."""
    render = getattr(child, "render", None)
    if callable(render):
        return render(width)
    raise TypeError(
        f"{type(child).__name__} is not renderable: it does not implement "
        "render(width) -> list[str]"
    )


class Component(ABC):  # noqa: B024 - see the either/or render contract below
    """
    Base class for all TUI components.

    A component implements one render contract:

    * ``render(width) -> list[str]`` — return styled ANSI lines. The string
      renderer (``tui/scrollback.py``) consumes them directly, with no
      per-character cell grid in between.

    Width-aware work that used to justify a cell grid — wrapping, splicing,
    measuring wide glyphs and multi-codepoint clusters — lives in
    ``tui/ansi_text.py``, which operates on styled grapheme tokens.
    """

    #: Where this component wants the text cursor after its last render, in
    #: its own coordinate space (row 0 == its first line). Only meaningful for
    #: focused, cursor-bearing components (currently TextInput). A container
    #: offsets a child's request by where that child starts, so the position
    #: survives however many nested containers sit above it.
    cursor_position: Position | None = None

    def render(self, width: int) -> list[str]:
        """Return this component's styled ANSI lines at ``width`` columns."""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement render(width) -> list[str]"
        )

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
        """Concatenate the children's lines.

        Defined explicitly so a container never forces its children back
        through the cell bridge: a migrated child stays on strings end to end
        even while its siblings have not moved yet.

        A child's cursor request is reported in its own coordinates, so it is
        offset by where that child starts here and re-published on the
        container — that is how a focused TextInput's cursor reaches the
        renderer through however many nested containers sit above it.
        """
        lines: list[str] = []
        self.cursor_position = None
        for child in self.children:
            start = len(lines)
            lines.extend(_child_lines(child, width))
            child_cursor = getattr(child, "cursor_position", None)
            if child_cursor is not None:
                self.cursor_position = Position(child_cursor.x, start + child_cursor.y)
        return lines

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

    def render(self, width: int) -> list[str]:
        from tau.tui.compose import wrap_to_rows

        out: list[str] = []
        for line in self._lines:
            out.extend(wrap_to_rows(line, width))
        return out


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

    def render(self, width: int) -> list[str]:
        from tau.tui.compose import wrap_to_rows

        content = self._style(self._text) if self._style is not None else self._text
        out: list[str] = []
        for line in content.split("\n"):
            out.extend(wrap_to_rows(line, width))
        return out


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
        out: list[str] = []
        for child in self.children:
            out.extend(_child_lines(child, width))
        return out

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

    def render(self, width: int) -> list[str]:
        from tau.tui.compose import composite_lines

        groups: dict[str, list[tuple[list[str], int]]] = {
            "left": [],
            "center": [],
            "right": [],
        }
        for component, align in self._slots:
            lines = _child_lines(component, width)
            # Only the first row is placed, matching the cell path's
            # blit of Rect(0, 0, width, 1) -- a Row is one row tall.
            head = lines[0] if lines else ""
            content_width = _content_width(head, width)
            groups[align if align in groups else "left"].append(([head], content_width))

        def group_width(group: list[tuple[list[str], int]]) -> int:
            return sum(w for _, w in group) + 2 * max(0, len(group) - 1)

        left_width = group_width(groups["left"])
        center_width = group_width(groups["center"])
        right_width = group_width(groups["right"])
        starts = {
            "left": 0,
            "center": max(left_width + 1, (width - center_width) // 2),
            "right": max(0, width - right_width),
        }
        if groups["center"] and starts["center"] + center_width > starts["right"]:
            starts["center"] = max(left_width + 1, starts["right"] - center_width - 1)

        out: list[str] = [""]
        for align in ("left", "center", "right"):
            column = starts[align]
            for index, (lines, w) in enumerate(groups[align]):
                if index:
                    column += 2
                if w:
                    out = composite_lines(out, lines, 0, column, w, width)
                column += w
        return out

    def handle_input(self, event: InputEvent) -> bool:
        return any(component.handle_input(event) for component, _ in self._slots)

    def invalidate(self) -> None:
        for component, _ in self._slots:
            component.invalidate()


def _content_width(line: str, width: int) -> int:
    """Columns of ``line`` that carry content, ignoring trailing blanks.

    A trailing *plain* space is not content, but a styled one is (it paints a
    background). Measuring with visible_width instead counts padding as content
    and pushes the next alignment group right.

    An inline-image line reports zero: its escape draws pixels but occupies no
    columns of the text grid.
    """
    from tau.tui.ansi_text import is_image_escape, tokenize
    from tau.tui.style import Style

    if not line or width <= 0:
        return 0
    if is_image_escape(line):
        return 0

    blank = Style()
    columns: list[tuple[str, Style]] = [(" ", blank)] * width
    column = 0
    for cluster, glyph_width, style in tokenize(line):
        if column + glyph_width > width:
            break
        columns[column] = (cluster, style)
        if glyph_width == 2 and column + 1 < width:
            columns[column + 1] = (" ", style)
        column += glyph_width

    content = 0
    for index, (symbol, style) in enumerate(columns):
        if symbol != " " or style != blank:
            content = index + 1
    return content


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
        from tau.tui.compose import composite_lines

        target = max(1, _resolve_width(self._width, width))
        lines = _child_lines(self._child, target)
        offset = 0
        if self._align == "center":
            offset = max(0, (width - target) // 2)
        elif self._align == "right":
            offset = max(0, width - target)
        return composite_lines([], lines, 0, offset, target, width)

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
        from tau.tui.compose import composite_lines

        widths = self._column_widths(width)
        rendered = [
            _child_lines(child, w) if w > 0 else []
            for (child, _), w in zip(self._slots, widths, strict=True)
        ]
        out: list[str] = []
        x = 0
        for lines, w in zip(rendered, widths, strict=True):
            if w > 0:
                out = composite_lines(out, lines, 0, x, w, width)
                x += w + self._gap
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

    Because ``render`` only receives the available *width*,
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

    def render(self, width: int) -> list[str]:
        rendered = [_child_lines(child, width) for child, _ in self._slots]
        heights = self._row_heights([len(lines) for lines in rendered])
        out: list[str] = []
        for index, (lines, height) in enumerate(zip(rendered, heights, strict=True)):
            if index and self._gap:
                out.extend([""] * self._gap)
            if height <= 0:
                continue
            # A child taller than its slot is clipped, shorter is padded, so
            # the next child always starts where the layout says it does.
            out.extend(lines[:height])
            out.extend([""] * max(0, height - len(lines)))
        return out

    def handle_input(self, event: InputEvent) -> bool:
        return any(child.handle_input(event) for child, _ in self._slots)

    def invalidate(self) -> None:
        for child, _ in self._slots:
            child.invalidate()
