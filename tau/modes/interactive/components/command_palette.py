from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import Style, apply_style
from tau.tui.text import Line, Span
from tau.tui.utils import fuzzy_filter
from tau.tui.widgets.list import List, ListItem, ListState

if True:  # avoid circular at runtime
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from tau.commands.types import CommandInfo
        from tau.tui.theme import SelectListTheme

VISIBLE_ROWS = 5


class CommandPalette(Component):
    """
    Fuzzy-filtered dropdown shown above the input when the user types '/'.
    Up/down arrows (and ctrl+p / ctrl+n) scroll selection.
    """

    def __init__(self, theme: SelectListTheme | None = None) -> None:
        self._all_commands: list[CommandInfo] = []
        self._commands: list[CommandInfo] = []
        self._selected = 0
        self._query = ""
        self._list_state = ListState()

        from tau.tui.theme import SelectListTheme as _ST

        self._theme = theme or _ST()

    def set_theme(self, theme: SelectListTheme) -> None:
        self._theme = theme

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return bool(self._commands)

    @property
    def selected(self) -> CommandInfo | None:
        if not self._commands:
            return None
        return self._commands[self._selected]

    @property
    def line_count(self) -> int:
        return min(VISIBLE_ROWS, len(self._commands))

    def set_commands(self, commands: list[CommandInfo]) -> None:
        """Replace the full command list and re-apply the current query."""
        self._all_commands = list(commands)
        self._apply_filter()

    def set_query(self, query: str) -> None:
        """Set the fuzzy query (typically the text after '/')."""
        if query == self._query:
            return
        self._query = query
        self._apply_filter()

    def move_up(self) -> None:
        if self._commands:
            self._selected = (self._selected - 1) % len(self._commands)

    def move_down(self) -> None:
        if self._commands:
            self._selected = (self._selected + 1) % len(self._commands)

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if not self._commands:
            return 0

        count = len(self._commands)
        visible = min(VISIBLE_ROWS, count)

        # Scroll so selected row is always in view, bottom-aligned when scrolled
        # (distinct from the centering formula the flat-list selectors use, and
        # from ListState.ensure_visible's minimal-scroll — kept as its own
        # explicit formula since List.render()'s own ensure_visible call is a
        # no-op on top of any offset already satisfying "selected is visible").
        start = max(0, min(self._selected - visible + 1, count - visible))

        # Label column width — longest "/name" in visible window, capped at 20
        label_w = max(
            8,
            min(
                max(len(f"/{c.name}") for c in self._commands[start : start + visible]),
                20,
            ),
        )
        desc_w = max(0, area.width - label_w - 4)  # 4 = "  " + " " + margin

        t = self._theme
        y = area.y

        def write(line: str) -> None:
            nonlocal y
            from tau.tui.ansi_bridge import parse_ansi_into
            from tau.tui.utils import visible_width, wrap

            for wl in wrap(line, area.width) if visible_width(line) > area.width else [line]:
                buf.grow_to(y + 1)
                parse_ansi_into(buf, area.x, y, wl, area.width)
                y += 1

        if start > 0:
            write(apply_style(t.indicator, f"  ↑ {start} more"))

        list_items: list[ListItem] = []
        for i, cmd in enumerate(self._commands):
            is_sel = i == self._selected
            name_str = f"/{cmd.name}"
            label = name_str[:label_w].ljust(label_w)
            desc = cmd.description[:desc_w] if desc_w > 0 else ""
            label_style = t.selected_label if is_sel else t.normal_label
            desc_style = t.selected_desc if is_sel else t.normal_desc
            spans = [Span("  ", Style()), Span(label, label_style), Span("  ", Style())]
            spans.append(Span(desc, desc_style))
            list_items.append(ListItem(Line(spans)))

        self._list_state.select(self._selected)
        self._list_state.offset = start
        list_area = Rect(area.x, y, area.width, visible)
        buf.grow_to(y + visible)
        widget = List(
            items=list_items,
            highlight_symbol="",
            highlight_style=t.selected_bg if t.selected_bg is not None else Style(),
        )
        widget.render(list_area, buf, self._list_state)
        y += visible

        remaining = count - (start + visible)
        if remaining > 0:
            write(apply_style(t.indicator, f"  ↓ {remaining} more"))

        return y - area.y

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        keybindings = get_keybindings()
        if keybindings.matches(event, "tui.select.up"):
            self.move_up()
            return True
        if keybindings.matches(event, "tui.select.down"):
            self.move_down()
            return True
        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _apply_filter(self) -> None:
        q = self._query.strip()
        if not q:
            self._commands = list(self._all_commands)
        else:
            self._commands = fuzzy_filter(
                self._all_commands,
                q,
                lambda c: c.name + " " + c.description,
            )
        if self._commands:
            self._selected = min(self._selected, len(self._commands) - 1)
        else:
            self._selected = 0
