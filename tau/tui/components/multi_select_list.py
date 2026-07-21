"""MultiSelectList: pick any number of items from a list.

The counterpart to ``select_list``'s single pick. Space toggles the row under
the cursor, Enter confirms the whole set, Esc cancels — so an empty result and
a cancelled one stay distinguishable (``[]`` vs ``None``).

Layering follows the rest of the TUI: this owns the state and the keys, while
the scroll window and row painting go through ``render_picker_cells`` and, under
that, the ``List`` widget in ``tui/widgets/``. Nothing interactive belongs in
``widgets/`` — those are immediate-mode painters that never read input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tau.tui.component import Component
from tau.tui.components.simple_picker import PickerRow, render_picker_cells
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent
from tau.tui.style import Style
from tau.tui.text import Span
from tau.tui.theme import LayoutTheme
from tau.tui.utils import wrap
from tau.tui.widgets.list import ListState

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer

#: Continuation rows sit under the label, past the arrow + checkbox columns.
DETAIL_INDENT = "      "

CHECKED_SYMBOL = "✔"
UNCHECKED_SYMBOL = "✖"

DEFAULT_HINT = "↑/↓ move  ·  Space toggle  ·  Enter confirm  ·  Esc cancel"


@dataclass
class MultiSelectItem:
    """One row. ``value`` is what comes back; it defaults to ``label``."""

    label: str
    description: str = ""
    value: Any = None
    checked: bool = False

    def resolved_value(self) -> Any:
        return self.label if self.value is None else self.value


@dataclass
class MultiSelectList(Component):
    """A checkbox list. Calls ``on_done`` with the chosen values, or ``None``.

    ``on_done`` receives a list — possibly empty, which is a real answer
    ("none of these") and must not be confused with the ``None`` that means
    the user dismissed the picker.
    """

    title: str
    items: list[MultiSelectItem]
    on_done: Any
    theme: LayoutTheme = field(default_factory=LayoutTheme)
    hint: str = DEFAULT_HINT
    max_visible: int = 10
    #: Reject Enter while nothing is ticked. Off by default — "none of these"
    #: is usually a legitimate answer.
    require_selection: bool = False

    def __post_init__(self) -> None:
        self._cursor = 0
        self._state = ListState()
        self._warning = ""

    # ── State ─────────────────────────────────────────────────────────────

    @property
    def checked_values(self) -> list[Any]:
        """Chosen values, in the order the items were given."""
        return [item.resolved_value() for item in self.items if item.checked]

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.items[index].checked = not self.items[index].checked
            self._warning = ""

    # ── Render ────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self.theme
        header = [f"  {self.title}"] if self.title else []
        if self._warning:
            header.append(f"  {self._warning}")

        # Descriptions go on their own wrapped rows beneath the label rather
        # than trailing it — at narrow widths a trailing description is just
        # truncated, and long ones are exactly where the detail matters.
        detail_width = max(area.width - len(DETAIL_INDENT) - 4, 20)

        rows: list[PickerRow] = []
        for item in self.items:
            symbol = CHECKED_SYMBOL if item.checked else UNCHECKED_SYMBOL
            details = (
                [f"{DETAIL_INDENT}{line}" for line in wrap(item.description, detail_width)]
                if item.description
                else []
            )
            rows.append(
                PickerRow(
                    label=item.label,
                    # Colour the box independently of the label, the way the
                    # /extensions config panel does.
                    prefix_spans=[
                        Span(symbol, t.success if item.checked else t.muted),
                        Span(" ", Style()),
                    ],
                    detail_lines=details,
                )
            )

        # Count first: the hint is long enough to wrap on a narrow terminal,
        # and the running total is the part worth keeping on screen.
        count = sum(1 for item in self.items if item.checked)
        hint = f"{count} selected  ·  {self.hint}" if count else self.hint

        return render_picker_cells(
            buf,
            area,
            header=header,
            rows=rows,
            selected=self._cursor,
            state=self._state,
            max_visible=self.max_visible,
            border_style=t.border,
            muted_style=t.muted,
            accent_style=t.accent,
            emphasis_style=t.emphasis,
            hint=hint,
            arrow=t.selector_arrow,
        )

    # ── Input ─────────────────────────────────────────────────────────────

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent) or not self.items:
            return False

        match event.key:
            case "up":
                self._cursor = (self._cursor - 1) % len(self.items)
            case "down":
                self._cursor = (self._cursor + 1) % len(self.items)
            case " " | "space":
                self.toggle(self._cursor)
            case "enter":
                if self.require_selection and not any(i.checked for i in self.items):
                    self._warning = "Select at least one option, or press Esc to cancel."
                    return True
                self.on_done(self.checked_values)
            case "escape":
                self.on_done(None)
            case _:
                return False
        return True

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self.theme = theme
