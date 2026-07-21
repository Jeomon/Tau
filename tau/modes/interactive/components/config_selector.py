"""Config selector — enable/disable extensions by scope."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tau.tui.ansi_bridge import parse_ansi_into
from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect
from tau.tui.input import InputEvent, KeyEvent, get_keybindings
from tau.tui.style import Style, apply_style
from tau.tui.text import Line, Span
from tau.tui.utils import rule
from tau.tui.widgets.list import List, ListItem, ListState

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_VISIBLE_ROWS = 12


@dataclass
class ConfigEntry:
    path: str  # raw path key, used for toggling/matching
    name: str  # extension name or title
    enabled: bool
    scope: Literal["global", "project", "builtin"]
    author: str | None = None
    path_display: str = ""  # pretty path shown in parentheses


ENABLED_SYMBOL = "✔"
DISABLED_SYMBOL = "✖"


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
        self._list_state = ListState()
        self._select_first_item()

    # ── Component ─────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        y = area.y

        def write(line: str) -> None:
            nonlocal y
            from tau.tui.utils import visible_width, wrap

            for wl in wrap(line, area.width) if visible_width(line) > area.width else [line]:
                buf.grow_to(y + 1)
                parse_ansi_into(buf, area.x, y, wl, area.width)
                y += 1

        write("  " + apply_style(t.emphasis, "Extensions"))
        divider = rule(area.width, t.border)
        write(divider)

        if self._search:
            write(f"  {apply_style(t.muted, '⊘')} {self._search}█")
        else:
            write("  " + apply_style(t.muted, "⊘ Search extensions…"))
        write(divider)

        if not self._filtered:
            write("  " + apply_style(t.muted, "No extensions found"))
            write(divider)
            write("  " + apply_style(t.muted, "Space: toggle  ·  Esc: close"))
            return y - area.y

        # Flat list with group headers — headers occupy real rows in the
        # List but are never selectable; sel_flat_idx (not self._selected,
        # which indexes _filtered only) is the flat row List.state tracks.
        flat = self._build_flat()
        selectable = [i for i, (kind, _) in enumerate(flat) if kind == "item"]
        sel_flat_idx = selectable[self._selected] if selectable else -1

        count = len(flat)
        visible = min(_VISIBLE_ROWS, count)
        start = max(0, min(sel_flat_idx - visible // 2, max(0, count - visible)))

        if start > 0:
            write("  " + apply_style(t.muted, f"↑ {start} more above"))

        list_items: list[ListItem] = []
        for i, (kind, payload) in enumerate(flat):
            if kind == "header":
                list_items.append(
                    ListItem(Line([Span("  ", Style()), Span(str(payload), t.accent)]))
                )
                continue
            assert isinstance(payload, ConfigEntry)
            is_sel = i == sel_flat_idx
            checkbox_style = t.success if payload.enabled else t.muted
            checkbox_symbol = ENABLED_SYMBOL if payload.enabled else DISABLED_SYMBOL
            name_style = t.emphasis if is_sel else t.muted

            if is_sel:
                spans = [Span("  ", Style()), Span(t.selector_arrow, t.accent), Span(" ", Style())]
            else:
                spans = [Span("    ", Style())]
            spans.append(Span(checkbox_symbol, checkbox_style))
            spans.append(Span(" ", Style()))
            spans.append(Span(payload.name, name_style))
            if payload.author:
                spans.append(Span(" ", Style()))
                spans.append(Span(f"by {payload.author}", t.muted))
            if payload.path_display:
                spans.append(Span(" ", Style()))
                spans.append(Span(f"({payload.path_display})", t.muted))
            list_items.append(ListItem(Line(spans)))

        self._list_state.select(sel_flat_idx if sel_flat_idx >= 0 else None)
        self._list_state.offset = start
        list_area = Rect(area.x, y, area.width, visible)
        buf.grow_to(y + visible)
        List(items=list_items, highlight_symbol="", highlight_style=Style()).render(
            list_area, buf, self._list_state
        )
        y += visible

        remaining = count - (start + visible)
        if remaining > 0:
            write("  " + apply_style(t.muted, f"↓ {remaining} more below"))

        write(divider)
        write("  " + apply_style(t.muted, "Space: toggle  ·  ↑/↓ to move  ·  Esc: close"))
        return y - area.y

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
        _SCOPE_LABELS = {"global": "Global", "project": "Project", "builtin": "Builtin"}
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
                e
                for e in self._all_entries
                if q in e.name.lower()
                or (e.author and q in e.author.lower())
                or q in e.path.lower()
                or q in e.scope.lower()
            ]
        self._selected = min(self._selected, max(0, len(self._filtered) - 1))

    def _select_first_item(self) -> None:
        self._selected = 0 if self._filtered else -1
