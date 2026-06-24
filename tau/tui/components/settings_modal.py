from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tau.tui.ansi import BOLD, BRIGHT_BLACK, BRIGHT_WHITE, DIM, RESET, cursor_block


@dataclass
class SettingItem:
    id: str
    label: str
    current_value: str
    description: str = ""
    values: list[str] = field(default_factory=list)
    submenu_items: list[str] = field(default_factory=list)
    submenu_title: str = ""
    text_input: bool = False
    submenu_settings: list[SettingItem] = field(default_factory=list)
    submenu_on_change: Callable[[str, str], None] | None = None


class SettingsModal:
    """Interactive settings list.

    - Up/Down  navigate rows
    - Enter/Space  cycle value, open submenu/sub-panel, or enter text-edit mode
    - Escape  cancel text-edit / close submenu / close modal
    - Type to fuzzy-search (or type into the edit buffer when editing)
    - Backspace removes last search char (or last edit char when editing)
    """

    def __init__(
        self,
        items: list[SettingItem],
        on_change: Callable[[str, str], None],
        max_visible: int = 10,
        title: str = "",
    ) -> None:
        self._all_items = items
        self._filtered: list[SettingItem] = list(items)
        self._on_change = on_change
        self._max_visible = max_visible
        self._title = title
        self._selected = 0
        self._search = ""

        # Submenu state — either a ListModal (for submenu_items) or a nested SettingsModal
        self._submenu: object | None = None
        self._submenu_id: str | None = None

        # Inline text-edit state
        self._editing = False
        self._edit_buffer = ""
        self._edit_id: str | None = None

    # ── Public state ──────────────────────────────────────────────────────────

    @property
    def in_submenu(self) -> bool:
        return self._submenu is not None or self._editing

    # ── Navigation ────────────────────────────────────────────────────────────

    def move_up(self) -> None:
        if self._editing:
            return
        if self._submenu is not None:
            self._submenu.move_up()  # type: ignore[attr-defined]
        elif self._filtered:
            self._selected = (self._selected - 1) % len(self._filtered)

    def move_down(self) -> None:
        if self._editing:
            return
        if self._submenu is not None:
            self._submenu.move_down()  # type: ignore[attr-defined]
        elif self._filtered:
            self._selected = (self._selected + 1) % len(self._filtered)

    def activate(self) -> None:
        """Enter/Space: confirm edit, activate submenu item, cycle value, or open sub-panel."""
        if self._editing:
            self._confirm_edit()
            return

        if self._submenu is not None:
            if isinstance(self._submenu, SettingsModal):
                self._submenu.activate()
            else:
                # ListModal: pick selected value and close
                val = self._submenu.selected_value()  # type: ignore[attr-defined]
                if val is not None and self._submenu_id is not None:
                    self._apply_value(self._submenu_id, val)
                self._submenu = None
                self._submenu_id = None
            return

        if not self._filtered:
            return

        item = self._filtered[self._selected]

        if item.submenu_settings:
            self._submenu = SettingsModal(
                item.submenu_settings,
                item.submenu_on_change or self._on_change,
                title=item.submenu_title or item.label,
            )
            self._submenu_id = item.id
        elif item.submenu_items:
            from tau.tui.components.modal import ListModal

            self._submenu = ListModal(
                item.submenu_items,
                item.current_value,
                item.submenu_title or item.label,
                item.description,
            )
            self._submenu_id = item.id
        elif item.text_input:
            self._editing = True
            self._edit_buffer = item.current_value
            self._edit_id = item.id
        elif item.values:
            try:
                idx = item.values.index(item.current_value)
                new_val = item.values[(idx + 1) % len(item.values)]
            except ValueError:
                new_val = item.values[0]
            self._apply_value(item.id, new_val)

    def cancel_submenu(self) -> None:
        if self._editing:
            self._editing = False
            self._edit_buffer = ""
            self._edit_id = None
        elif isinstance(self._submenu, SettingsModal) and self._submenu.in_submenu:
            # Delegate Escape inward so nested edit/submenu closes first
            self._submenu.cancel_submenu()
        else:
            self._submenu = None
            self._submenu_id = None

    # ── Search / text-edit input ──────────────────────────────────────────────

    def append_search(self, ch: str) -> None:
        if self._editing:
            self._edit_buffer += ch
            return
        if isinstance(self._submenu, SettingsModal):
            self._submenu.append_search(ch)
            return
        if self._submenu is not None:
            return
        self._search += ch
        self._refilter()

    def backspace_search(self) -> None:
        if self._editing:
            self._edit_buffer = self._edit_buffer[:-1]
            return
        if isinstance(self._submenu, SettingsModal):
            self._submenu.backspace_search()
            return
        if self._submenu is not None:
            return
        if self._search:
            self._search = self._search[:-1]
            self._refilter()

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        if self._submenu is not None:
            return self._submenu.render(width)  # type: ignore[attr-defined]

        divider = BRIGHT_BLACK + "─" * width + RESET
        lines: list[str] = []

        if self._title:
            lines.append(f"  {BOLD}{self._title}{RESET}")
            lines.append(divider)

        if self._editing:
            lines.append(f"  {DIM}editing — enter to confirm, esc to cancel{RESET}")
        elif self._search:
            lines.append(f"  {DIM}/{RESET}{self._search}█")
        else:
            lines.append(f"  {DIM}type to search…{RESET}")
        lines.append(divider)

        if not self._filtered:
            lines.append(f"  {DIM}No matching settings{RESET}")
        else:
            max_label = min(28, max(len(i.label) for i in self._filtered))
            count = len(self._filtered)
            visible = min(self._max_visible, count)
            start = max(0, min(self._selected - visible // 2, count - visible))

            if start > 0:
                lines.append(f"  {DIM}↑ {start} more{RESET}")

            for i in range(start, min(start + visible, count)):
                item = self._filtered[i]
                is_sel = i == self._selected
                label_padded = item.label.ljust(max_label)
                has_submenu = bool(item.submenu_items or item.submenu_settings)

                if is_sel and self._editing:
                    val_display = self._edit_buffer + cursor_block()
                    row = (
                        f"  {BOLD}{BRIGHT_WHITE}→ {label_padded}{RESET}"
                        f"  {BRIGHT_WHITE}{val_display}{RESET}"
                    )
                else:
                    val_display = (
                        (item.current_value.replace("_", " ") + " ▸")
                        if has_submenu
                        else item.current_value.replace("_", " ")
                    )
                    if is_sel:
                        row = (
                            f"  {BOLD}{BRIGHT_WHITE}→ {label_padded}{RESET}"
                            f"  {BRIGHT_WHITE}{val_display}{RESET}"
                        )
                    else:
                        row = f"    {DIM}{label_padded}{RESET}  {val_display}"
                lines.append(row)

            remaining = count - (start + visible)
            if remaining > 0:
                lines.append(f"  {DIM}↓ {remaining} more{RESET}")

        lines.append(divider)
        desc = ""
        if self._filtered and 0 <= self._selected < len(self._filtered):
            desc = self._filtered[self._selected].description
        lines.append(f"  {DIM}{desc}{RESET}" if desc else f"  {DIM}—{RESET}")

        lines.append(divider)
        if self._editing:
            lines.append(f"  {DIM}enter confirm  esc cancel{RESET}")
        elif self._title:
            lines.append(f"  {DIM}↑/↓ move  enter/spc toggle  esc back  type to search{RESET}")
        else:
            lines.append(
                f"  {DIM}↑/↓ move  enter/spc toggle  esc save & close  type to search{RESET}"
            )

        return lines

    # ── Internal ──────────────────────────────────────────────────────────────

    def _confirm_edit(self) -> None:
        buf = self._edit_buffer.strip()
        edit_id = self._edit_id
        self._editing = False
        self._edit_buffer = ""
        self._edit_id = None
        if edit_id and buf:
            self._apply_value(edit_id, buf)

    def _apply_value(self, item_id: str, val: str) -> None:
        for item in self._all_items:
            if item.id == item_id:
                item.current_value = val
                break
        for item in self._filtered:
            if item.id == item_id:
                item.current_value = val
                break
        self._on_change(item_id, val)

    def _refilter(self) -> None:
        if not self._search:
            self._filtered = list(self._all_items)
        else:
            q = self._search.lower()
            self._filtered = [i for i in self._all_items if q in i.label.lower()]
        self._selected = min(self._selected, max(0, len(self._filtered) - 1))
