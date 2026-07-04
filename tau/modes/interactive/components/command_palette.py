from __future__ import annotations

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import apply_style
from tau.tui.utils import fuzzy_filter, visible_width

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

    def render(self, width: int) -> list[str]:
        if not self._commands:
            return []

        count = len(self._commands)
        visible = min(VISIBLE_ROWS, count)

        # Scroll so selected row is always in view
        start = max(0, min(self._selected - visible + 1, count - visible))

        # Label column width — longest "/name" in visible window, capped at 20
        label_w = max(
            8,
            min(
                max(len(f"/{c.name}") for c in self._commands[start : start + visible]),
                20,
            ),
        )
        desc_w = max(0, width - label_w - 4)  # 4 = "  " + " " + margin

        t = self._theme
        lines: list[str] = []

        # Scroll-up indicator
        if start > 0:
            lines.append(apply_style(t.indicator, f"  ↑ {start} more"))

        for i in range(start, start + visible):
            cmd = self._commands[i]
            is_sel = i == self._selected

            name_str = f"/{cmd.name}"
            label = name_str[:label_w].ljust(label_w)
            desc = cmd.description[:desc_w] if desc_w > 0 else ""

            if is_sel:
                label_s = apply_style(t.selected_label, label)
                desc_s = apply_style(t.selected_desc, desc)
                row = f"  {label_s}  {desc_s}"
                if t.selected_bg:
                    fill = max(0, width - visible_width(row))
                    row = apply_style(t.selected_bg, row + " " * fill)
            else:
                label_s = apply_style(t.normal_label, label)
                desc_s = apply_style(t.normal_desc, desc)
                row = f"  {label_s}  {desc_s}"

            lines.append(row)

        # Scroll-down indicator
        remaining = count - (start + visible)
        if remaining > 0:
            lines.append(apply_style(t.indicator, f"  ↓ {remaining} more"))

        return lines

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
