"""Settings components — SettingsSelector, SettingItem, ListSelector, and build_manifest_panel."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import is_window_focused, rule

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme


# ── ListSelector ──────────────────────────────────────────────────────────────


class ListSelector:
    """Submenu list picker used by SettingsSelector for submenu_items rows."""

    def __init__(
        self,
        items: list[str],
        current: str,
        title: str,
        subtitle: str = "",
        on_preview: Callable[[str], None] | None = None,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._items = list(items)
        self._current = current
        self._title = title
        self._subtitle = subtitle
        self._preview = on_preview
        self._selected = 0

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._theme = theme

        for i, it in enumerate(self._items):
            if it == current:
                self._selected = i
                break

    def move_up(self) -> None:
        if self._items:
            self._selected = (self._selected - 1) % len(self._items)
            if self._preview:
                self._preview(self._items[self._selected])

    def move_down(self) -> None:
        if self._items:
            self._selected = (self._selected + 1) % len(self._items)
            if self._preview:
                self._preview(self._items[self._selected])

    def selected_value(self) -> str | None:
        if not self._items:
            return None
        return self._items[self._selected]

    def set_theme(self, theme: LayoutTheme) -> None:
        """Apply a new theme while the submenu remains open."""
        self._theme = theme

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        t = self._theme
        row = area.y

        def write(spans: list[Span]) -> None:
            nonlocal row
            buf.grow_to(row + 1)
            buf.set_line(area.x, row, Line(spans), area.width)
            row += 1

        write([Span("  "), Span(self._title, t.emphasis)])
        if self._subtitle:
            write([Span("  "), Span(self._subtitle, t.muted)])

        write([Span(rule(area.width), t.border)])

        if not self._items:
            write([Span("  "), Span("(no items)", t.muted)])
        else:
            for i, item in enumerate(self._items):
                is_sel = i == self._selected
                is_current = item == self._current
                if is_sel:
                    spans = [
                        Span("  "),
                        Span(t.selector_arrow, t.accent),
                        Span(" "),
                        Span(item, t.emphasis),
                    ]
                else:
                    spans = [Span("    "), Span(item, t.muted)]
                if is_current:
                    spans.extend([Span(" "), Span("✓", t.success)])
                write(spans)

        write([Span(rule(area.width), t.border)])
        write([Span("  "), Span("↑/↓ move  ·  enter select  ·  esc cancel", t.muted)])

        return row - area.y


# ── SettingsSelector ──────────────────────────────────────────────────────────


@dataclass
class SettingItem:
    id: str
    label: str
    current_value: str
    description: str = ""
    values: list[str] = field(default_factory=list)
    submenu_items: list[str] = field(default_factory=list)
    submenu_title: str = ""
    submenu_on_preview: Callable[[str], None] | None = None
    submenu_on_cancel: Callable[[], None] | None = None
    text_input: bool = False
    submenu_settings: list[SettingItem] = field(default_factory=list)
    submenu_on_change: Callable[[str, str], None] | None = None


class SettingsSelector:
    """Interactive settings list with tab bar, search box, and two-column layout.

    - Tab            cycle through tabs (when tabs provided)
    - Up/Down        navigate rows
    - Enter/Space    cycle value, open submenu/sub-panel, or enter text-edit mode
    - Escape         cancel text-edit / close submenu / close modal
    - Type to fuzzy-search (or type into the edit buffer when editing)
    - Backspace      removes last search char (or last edit char when editing)
    """

    def __init__(
        self,
        items: list[SettingItem],
        on_change: Callable[[str, str], None],
        max_visible: int = 10,
        title: str = "",
        theme: LayoutTheme | None = None,
        tabs: list[tuple[str, list[SettingItem]]] | None = None,
    ) -> None:
        self._tabs: list[tuple[str, list[SettingItem]]] = tabs or []
        self._active_tab = 0
        self._on_change = on_change
        self._max_visible = max_visible
        self._title = title
        self._selected = 0
        self._search = ""

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._theme = theme

        source = self._tabs[0][1] if self._tabs else items
        self._all_items: list[SettingItem] = list(source)
        self._filtered: list[SettingItem] = list(self._all_items)

        self._submenu: object | None = None
        self._submenu_id: str | None = None
        self._submenu_on_cancel: Callable[[], None] | None = None

        self._editing = False
        self._edit_buffer = ""
        self._edit_id: str | None = None

    # ── Tab navigation ────────────────────────────────────────────────────────

    def next_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab = (self._active_tab + 1) % len(self._tabs)
        self._switch_tab()

    def prev_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab = (self._active_tab - 1) % len(self._tabs)
        self._switch_tab()

    def _switch_tab(self) -> None:
        _, tab_items = self._tabs[self._active_tab]
        self._all_items = list(tab_items)
        self._search = ""
        self._selected = 0
        self._submenu = None
        self._submenu_id = None
        self._submenu_on_cancel = None
        self._editing = False
        self._edit_buffer = ""
        self._edit_id = None
        self._refilter()

    # ── Public state ──────────────────────────────────────────────────────────

    @property
    def in_submenu(self) -> bool:
        return self._submenu is not None or self._editing

    @property
    def is_editing(self) -> bool:
        return self._editing

    def set_theme(self, theme: LayoutTheme) -> None:
        """Apply a new theme to this selector and its active submenu."""
        self._theme = theme
        setter = getattr(self._submenu, "set_theme", None)
        if callable(setter):
            setter(theme)

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
            if isinstance(self._submenu, SettingsSelector):
                self._submenu.activate()
            else:
                val = self._submenu.selected_value()  # type: ignore[attr-defined]
                if val is not None and self._submenu_id is not None:
                    self._apply_value(self._submenu_id, val)
                self._submenu = None
                self._submenu_id = None
                self._submenu_on_cancel = None
            return

        if not self._filtered:
            return

        item = self._filtered[self._selected]

        if item.submenu_settings:
            self._submenu = SettingsSelector(
                item.submenu_settings,
                item.submenu_on_change or self._on_change,
                title=item.submenu_title or item.label,
                theme=self._theme,
            )
            self._submenu_id = item.id
        elif item.submenu_items:
            self._submenu = ListSelector(
                item.submenu_items,
                item.current_value,
                item.submenu_title or item.label,
                item.description,
                on_preview=item.submenu_on_preview,
                theme=self._theme,
            )
            self._submenu_id = item.id
            self._submenu_on_cancel = item.submenu_on_cancel
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
        elif isinstance(self._submenu, SettingsSelector) and self._submenu.in_submenu:
            self._submenu.cancel_submenu()
        else:
            if self._submenu_on_cancel is not None:
                self._submenu_on_cancel()
            self._submenu = None
            self._submenu_id = None
            self._submenu_on_cancel = None

    # ── Search / text-edit input ──────────────────────────────────────────────

    def append_search(self, ch: str) -> None:
        if self._editing:
            self._edit_buffer += ch
            return
        if isinstance(self._submenu, SettingsSelector):
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
        if isinstance(self._submenu, SettingsSelector):
            self._submenu.backspace_search()
            return
        if self._submenu is not None:
            return
        if self._search:
            self._search = self._search[:-1]
            self._refilter()

    # ── Render ────────────────────────────────────────────────────────────────

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if self._submenu is not None:
            if isinstance(self._submenu, (SettingsSelector, ListSelector)):
                return self._submenu.render_cells(area, buf)
            raise TypeError(f"Unsupported settings submenu: {type(self._submenu).__name__}")

        t = self._theme
        row = area.y

        def write(spans: list[Span]) -> None:
            nonlocal row
            buf.grow_to(row + 1)
            buf.set_line(area.x, row, Line(spans), area.width)
            row += 1

        def text(content: str, style: Style | None = None, prefix: str = "") -> None:
            write([Span(prefix), Span(content, style or Style())])

        def divider() -> None:
            text(rule(area.width), t.border)

        # ── Tab bar ────────────────────────────────────────────────────────────
        if self._tabs:
            spans = [Span("  ")]
            for i, (label, _) in enumerate(self._tabs):
                if i:
                    spans.append(Span("  "))
                spans.append(
                    Span(
                        f"[{label}]" if i == self._active_tab else label,
                        t.emphasis if i == self._active_tab else t.muted,
                    )
                )
            write(spans)
        elif self._title:
            text(self._title, t.emphasis, "  ")
        divider()

        # ── Search box ─────────────────────────────────────────────────────────
        if self._editing:
            text("editing — enter to confirm, esc to cancel", t.muted, "  ")
        elif self._search:
            cursor_style = Style().reversed() if is_window_focused() else Style()
            write(
                [
                    Span("  "),
                    Span("⊘", t.muted),
                    Span(f" {self._search}"),
                    Span(" ", cursor_style),
                ]
            )
        else:
            text("⊘ Search settings…", t.muted, "  ")
        divider()

        # ── Items list ─────────────────────────────────────────────────────────
        if not self._filtered:
            text("No matching settings", t.muted, "  ")
        else:
            max_label = min(28, max(len(i.label) for i in self._filtered))
            count = len(self._filtered)
            visible = min(self._max_visible, count)
            start = max(0, min(self._selected - visible // 2, count - visible))

            if start > 0:
                text(f"↑ {start} more above", t.muted, "  ")

            for i in range(start, min(start + visible, count)):
                item = self._filtered[i]
                is_sel = i == self._selected
                label_padded = item.label.ljust(max_label)
                has_submenu = bool(item.submenu_items or item.submenu_settings)

                val_display = (
                    (item.current_value.replace("_", " ") + " ▸")
                    if has_submenu
                    else item.current_value.replace("_", " ")
                )

                if is_sel and self._editing:
                    cursor_style = Style().reversed() if is_window_focused() else Style()
                    write(
                        [
                            Span("  "),
                            Span(t.selector_arrow, t.accent),
                            Span(" "),
                            Span(label_padded, t.emphasis),
                            Span("  "),
                            Span(self._edit_buffer, t.emphasis),
                            Span(" ", t.emphasis.patch(cursor_style)),
                        ]
                    )
                elif is_sel:
                    write(
                        [
                            Span("  "),
                            Span(t.selector_arrow, t.accent),
                            Span(" "),
                            Span(label_padded, t.emphasis),
                            Span("  "),
                            Span(val_display, t.accent),
                        ]
                    )
                else:
                    write([Span("    "), Span(label_padded, t.muted), Span(f"  {val_display}")])

            remaining = count - (start + visible)
            if remaining > 0:
                text(f"↓ {remaining} more below", t.muted, "  ")

        divider()

        # ── Description of selected item ───────────────────────────────────────
        desc = ""
        if self._filtered and 0 <= self._selected < len(self._filtered):
            desc = self._filtered[self._selected].description
        text(desc if desc else "—", t.muted, "  ")
        divider()

        # ── Status bar ─────────────────────────────────────────────────────────
        if self._editing:
            text("enter: confirm  ·  esc: cancel", t.muted, "  ")
        else:
            tab_hint = "  ·  tab: switch" if len(self._tabs) > 1 else ""
            back_or_close = "back" if (self._title or self._tabs) else "close & save"
            hint_text = (
                f"Enter/Space to change  ·  / to search{tab_hint}  ·  Esc to {back_or_close}"
            )
            text(hint_text, t.muted, "  ")

        return row - area.y

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


# ── build_manifest_panel ──────────────────────────────────────────────────────

"""Build a ``/settings`` sub-panel for an extension from a declarative manifest schema.

Extensions can describe their settings in ``manifest.json`` instead of writing
imperative ``register_settings`` code. The framework reads the schema, builds the
panel, reads current values from the extension's config, and wires an ``on_change``
that — only when a value actually changed — persists to settings.json and reloads
just that extension so the change applies live.

Manifest shape (under the app key, e.g. ``"tau"``)::

    "settings": {
      "title": "Web search",
      "fields": [
        {"key": "engine", "label": "Search engine", "type": "enum",
         "values": ["ddgs", "exa", "tavily"], "default": "ddgs"},

        {"key": "exa", "label": "Exa", "type": "group", "fields": [
          {"key": "api_key", "label": "API key", "type": "secret"},
          {"key": "results", "label": "Results", "type": "int",
           "default": 10, "min": 1, "max": 50}
        ]}
      ]
    }

Field ``type`` values:
  group           nested sub-panel; ``fields`` are rendered one level deeper and
                  their keys are prefixed with the group key (``exa.api_key``)
  enum / select   cycle through ``values`` (required, non-empty)
  bool            toggle off/on (stored in config as a JSON boolean)
  int             numeric text input; optional ``min`` / ``max`` clamp
  string / secret free text input; optional ``pattern`` (regex) the value must match

Keys support dot-notation directly too (``"exa.api_key"``). Unknown types and
malformed fields are skipped with a logged warning rather than rendering a
misleading control.
"""

_log = logging.getLogger(__name__)

_LEAF_TYPES = {"enum", "select", "bool", "int", "string", "secret", "text"}


def refresh_current_values(items: list[Any], config: dict) -> None:
    """Update each SettingItem's current_value in-place from *config*.

    Called when /settings opens so extension panels always show the values
    stored in settings.json rather than the stale snapshot captured at
    extension load time (e.g. if settings.json was corrupt at startup).
    Item ids are dot-notation keys, matching the shape _get_nested reads.
    """
    for item in items:
        if item.submenu_settings:
            refresh_current_values(item.submenu_settings, config)
            continue
        raw = _get_nested(config, item.id, None)
        if raw is None:
            continue
        if item.values == ["off", "on"]:
            stored = raw is True or str(raw).lower() in ("true", "on")
            item.current_value = "on" if stored else "off"
        elif item.text_input:
            item.current_value = str(raw)
        elif item.values:
            s = str(raw)
            if s in item.values:
                item.current_value = s


def _get_nested(d: dict, path: str, default: Any = "") -> Any:
    obj: Any = d
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return default
        obj = obj[part]
    return obj if obj is not None else default


def _coerce(field_type: str, value: str, field: dict) -> Any:
    """Convert the panel's string value to the type the config should store."""
    if field_type == "bool":
        return str(value).lower() in ("on", "true")
    if field_type == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return field.get("default", 0)
        lo, hi = field.get("min"), field.get("max")
        if isinstance(lo, int):
            n = max(lo, n)
        if isinstance(hi, int):
            n = min(hi, n)
        return n
    return value


def _valid(field_type: str, value: str, field: dict) -> bool:
    """Reject an incoming value that violates the field's declared constraints."""
    if field_type == "int":
        try:
            int(value)
        except (TypeError, ValueError):
            return False
    if field_type in ("string", "secret", "text"):
        pattern = field.get("pattern")
        if pattern and not re.fullmatch(pattern, value or ""):
            return False
    return True


def build_manifest_panel(
    schema: dict,
    config: dict,
    *,
    default_title: str,
    apply: Callable[[str, Any], None],
) -> Any:
    """Construct an :class:`ExtensionSettingsRegistration` from a manifest schema.

    ``apply(key, value)`` is called with the full dot-path key and the coerced
    value when — and only when — a field's value actually changes. Returns
    ``None`` if the schema yields no usable items.
    """
    from tau.extensions.api import ExtensionSettingsRegistration

    field_defs: dict[str, dict] = {}  # full key -> field def (for coerce/validate)
    currents: dict[str, Any] = {}  # full key -> current config value (for diff)

    def build_items(fields: list, prefix: str) -> list[SettingItem]:
        items: list[SettingItem] = []
        for f in fields:
            if not isinstance(f, dict):
                _log.warning("settings_schema: skipping non-object field %r", f)
                continue
            key = f.get("key")
            if not key:
                _log.warning("settings_schema: skipping field with no 'key': %r", f)
                continue
            full = f"{prefix}.{key}" if prefix else key
            label = str(f.get("label") or key)
            description = str(f.get("description") or "")
            ftype = str(f.get("type") or "string").lower()

            # ── Nested group → sub-panel ──────────────────────────────────────
            if ftype == "group" or "fields" in f:
                children = build_items(f.get("fields") or [], full)
                if children:
                    items.append(
                        SettingItem(
                            id=full,
                            label=label,
                            description=description,
                            current_value="→",
                            submenu_title=str(f.get("title") or label),
                            submenu_settings=children,
                        )
                    )
                continue

            if ftype not in _LEAF_TYPES:
                _log.warning(
                    "settings_schema: unknown field type %r for %r — skipping", ftype, full
                )
                continue
            if ftype in ("enum", "select") and not f.get("values"):
                _log.warning("settings_schema: enum field %r has no values — skipping", full)
                continue

            current = _get_nested(config, full, f.get("default", ""))
            field_defs[full] = f

            if ftype in ("enum", "select"):
                currents[full] = current
                items.append(
                    SettingItem(
                        id=full,
                        label=label,
                        description=description,
                        current_value=str(current),
                        values=[str(v) for v in f.get("values", [])],
                    )
                )
            elif ftype == "bool":
                stored = current is True or str(current).lower() in ("true", "on")
                currents[full] = stored  # config-space bool, matches _coerce output
                items.append(
                    SettingItem(
                        id=full,
                        label=label,
                        description=description,
                        current_value="on" if stored else "off",
                        values=["off", "on"],
                    )
                )
            else:  # string, secret, int, text
                currents[full] = current
                items.append(
                    SettingItem(
                        id=full,
                        label=label,
                        description=description,
                        current_value=str(current if current is not None else ""),
                        text_input=True,
                    )
                )
        return items

    items = build_items(schema.get("fields") or [], "")
    if not items:
        return None

    # Surface the first top-level bool (e.g. a master "Enabled" switch) as an
    # on/off summary on the extension's parent row in the main /settings list.
    summary = ""
    summary_key = ""
    for f in schema.get("fields") or []:
        if isinstance(f, dict) and str(f.get("type") or "").lower() == "bool" and f.get("key"):
            summary_key = f["key"]
            summary = "on" if currents.get(summary_key) else "off"
            break

    def on_change(key: str, value: str) -> None:
        field = field_defs.get(key)
        if field is None:
            return  # not a leaf field we built (e.g. a group row) — ignore
        ftype = str(field.get("type") or "string").lower()
        if not _valid(ftype, value, field):
            _log.warning("settings_schema: invalid value %r for %r — ignored", value, key)
            return
        coerced = _coerce(ftype, value, field)
        if coerced == currents.get(key):
            return  # no change vs in-memory config — skip persist + reload
        currents[key] = coerced
        apply(key, coerced)

    return ExtensionSettingsRegistration(
        title=schema.get("title") or default_title,
        items=items,
        on_change=on_change,
        summary=summary,
        summary_key=summary_key,
    )
