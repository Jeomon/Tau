"""Config selector — enable/disable extensions by scope."""
from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from tau.tui.component import Component
from tau.tui.input import InputEvent, KeyEvent, get_keybindings

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 12


@dataclass
class ConfigEntry:
    path: str  # raw path key, used for toggling/matching
    name: str  # extension name or title
    enabled: bool
    scope: Literal["global", "project"]
    author: str | None = None
    path_display: str = ""  # pretty path shown in parentheses


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", s)


def _visible_len(s: str) -> int:
    return len(_strip_ansi(s))


class ConfigSelector(Component):
    """Enable/disable extensions across global and project scopes.

    Space or Enter toggles the highlighted entry. The toggle is written back
    immediately via the on_toggle callback. Escape closes the selector.
    """

    def __init__(
        self,
        entries: list[ConfigEntry],
        on_toggle: Callable[[ConfigEntry, bool], None],
        on_close: Callable[[], None],
        theme: LayoutTheme | None = None,
    ) -> None:
        from tau.tui.theme import LayoutTheme as LT

        self._all_entries = list(entries)
        self._filtered: list[ConfigEntry] = list(entries)
        self._on_toggle = on_toggle
        self._on_close = on_close
        self._theme = theme or LT()
        self._search = ""
        self._selected = 0
        self._select_first_item()

    # ── Component ─────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = t.border("─" * width)
        lines: list[str] = []

        # Header
        title = t.emphasis("Extensions")
        hint = t.muted("space toggle  esc close")
        gap = max(1, width - _visible_len(title) - _visible_len(hint) - 4)
        lines.append(f"  {title}{' ' * gap}{hint}")
        lines.append("  " + t.muted("Type to filter"))
        lines.append(divider)

        # Search bar
        if self._search:
            lines.append("  " + t.muted(f"/{self._search}█"))

        if not self._filtered:
            lines.append("  " + t.muted("No extensions found"))
            lines.append(divider)
            return lines

        # Flat list with group headers
        flat = self._build_flat()
        selectable = [i for i, (kind, _) in enumerate(flat) if kind == "item"]
        sel_flat_idx = selectable[self._selected] if selectable else -1

        count = len(flat)
        visible = min(_VISIBLE_ROWS, count)
        start = max(0, min(sel_flat_idx - visible // 2, count - visible))

        if start > 0:
            lines.append("  " + t.muted(f"↑ more"))

        for i in range(start, min(start + visible, count)):
            kind, payload = flat[i]
            if kind == "header":
                lines.append("  " + t.accent(str(payload)))
            else:
                assert isinstance(payload, ConfigEntry)
                is_sel = i == sel_flat_idx
                checkbox = t.success("[✓]") if payload.enabled else t.muted("[ ]")
                name = t.emphasis(payload.name) if is_sel else payload.name
                label = name
                if payload.author:
                    label += " " + t.muted(f"by {payload.author}")
                if payload.path_display:
                    label += " " + t.muted(f"({payload.path_display})")
                cursor = "→ " if is_sel else "  "
                cursor_styled = t.emphasis(cursor) if is_sel else cursor
                lines.append(f"  {cursor_styled}{checkbox} {label}")

        remaining = count - (start + visible)
        if remaining > 0:
            lines.append("  " + t.muted(f"↓ more"))

        lines.append(divider)
        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False

        kb = get_keybindings()

        if kb.matches(event, "tui.select.up"):
            self._move(-1)
            return True

        if kb.matches(event, "tui.select.down"):
            self._move(1)
            return True

        if kb.matches(event, "tui.select.confirm") or event.key == " ":
            self._toggle_selected()
            return True

        if kb.matches(event, "tui.select.dismiss"):
            self._on_close()
            return True

        if event.key == "backspace":
            if self._search:
                self._search = self._search[:-1]
                self._refilter()
            return True

        # Printable char → search
        if event.key and len(event.key) == 1 and event.key.isprintable():
            self._search += event.key
            self._refilter()
            return True

        return False

    def invalidate(self) -> None:
        pass

    def set_theme(self, theme: LayoutTheme) -> None:
        self._theme = theme

    # ── Search ────────────────────────────────────────────────────────────────

    def append_search(self, ch: str) -> None:
        self._search += ch
        self._refilter()

    def backspace_search(self) -> None:
        if self._search:
            self._search = self._search[:-1]
            self._refilter()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_flat(self) -> list[tuple[str, str | ConfigEntry]]:
        """Return interleaved header + item entries for rendering."""
        flat: list[tuple[str, str | ConfigEntry]] = []
        current_scope: str | None = None
        _SCOPE_LABELS = {"global": "Global", "project": "Project"}
        for entry in self._filtered:
            if entry.scope != current_scope:
                current_scope = entry.scope
                flat.append(("header", _SCOPE_LABELS.get(entry.scope, entry.scope)))
            flat.append(("item", entry))
        return flat

    def _selectable_entries(self) -> list[ConfigEntry]:
        return list(self._filtered)

    def _move(self, direction: int) -> None:
        if not self._filtered:
            return
        self._selected = max(0, min(len(self._filtered) - 1, self._selected + direction))

    def _toggle_selected(self) -> None:
        if not self._filtered:
            return
        entry = self._filtered[self._selected]
        new_enabled = not entry.enabled
        entry.enabled = new_enabled
        # Mirror in _all_entries
        for e in self._all_entries:
            if e.path == entry.path and e.scope == entry.scope:
                e.enabled = new_enabled
                break
        self._on_toggle(entry, new_enabled)

    def _refilter(self) -> None:
        q = self._search.lower()
        if not q:
            self._filtered = list(self._all_entries)
        else:
            self._filtered = [
                e for e in self._all_entries
                if q in e.name.lower()
                or (e.author and q in e.author.lower())
                or q in e.path.lower()
                or q in e.scope.lower()
            ]
        self._selected = min(self._selected, max(0, len(self._filtered) - 1))

    def _select_first_item(self) -> None:
        self._selected = 0 if self._filtered else -1
