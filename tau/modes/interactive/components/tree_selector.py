from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Literal, TypeVar

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line, Span
from tau.tui.utils import fuzzy_filter

T = TypeVar("T")

FilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]

_TOOL_ROLES = frozenset(
    {
        "tool",
        "terminal",
        "terminal_execution",
        "error",
        "skill",
        "template",
    }
)

_SETTINGS_ROLES = frozenset({"label", "model", "thinking_level"})


@dataclass
class TreeRow[T]:
    """A single row in a TreeSelectList: tree connectors + a role-colored snippet."""

    prefix: str
    role: str
    text: str
    on_active_path: bool = False
    is_current: bool = False
    selectable: bool = True
    value: T | None = None
    parent_value: T | None = None  # actual parent entry ID (for visible-tree maps)
    has_children: bool = False  # has children in the full (unfiltered) tree
    label: str | None = None
    label_timestamp: str | None = None
    search_text: str = field(default="")

    def __post_init__(self) -> None:
        if not self.search_text:
            self.search_text = f"{self.role} {self.text}"


class TreeSelectList[T]:
    """
    Picker that renders full pre-built rows (tree connectors + role-colored
    content) on one line each.  Supports fuzzy search, filter modes, folding,
    labels, and centered-viewport scrolling.
    """

    def __init__(
        self,
        rows: list[TreeRow[T]],
        role_style: Callable[[str, str], Style],
        accent_style: Style,
        dim_style: Style,
        max_visible: int = 10,
        selected_bg: Style | None = None,
        on_label_change: Callable[[T, str | None], None] | None = None,
    ) -> None:
        self._all_rows: list[TreeRow[T]] = rows
        self._filtered: list[TreeRow[T]] = list(rows)
        self._role_style = role_style
        self._accent_style = accent_style
        self._dim_style = dim_style
        self._max_visible = max(1, max_visible)
        self._selected_bg = selected_bg
        self._on_label_change = on_label_change

        # Selection state
        self._selected = next((i for i, r in enumerate(rows) if r.is_current), 0)
        self._last_selected_value: T | None = None

        # Search / filter
        self._query = ""
        self._filter_mode: FilterMode = "default"

        # Fold state
        self._folded_nodes: set[T] = set()

        # Visible tree maps (rebuilt after each filter/fold change)
        # These maps reflect which nodes are VISIBLE (not filtered or folded away).
        self._visible_parent_map: dict[T, T | None] = {}
        self._visible_children_map: dict[T | None, list[T]] = {}

        # In-memory label store: value → (label_text, iso_timestamp)
        self._labels: dict[T, tuple[str, str]] = {}
        self._show_label_timestamps: bool = False

        # Label editing state
        self._label_editing: bool = False
        self._label_input: str = ""
        self._label_editing_value: T | None = None

        self._apply_filter()

    # ------------------------------------------------------------------
    # Public interface (duck-types SelectList subset used by InlineSelector)
    # ------------------------------------------------------------------

    @property
    def selected_item(self) -> TreeRow[T] | None:
        if not self._filtered:
            return None
        return self._filtered[self._selected]

    @property
    def label_editing(self) -> bool:
        return self._label_editing

    def move_up(self) -> None:
        self._move(-1)

    def move_down(self) -> None:
        self._move(1)

    def page_up(self) -> None:
        self._selected = max(0, self._selected - self._max_visible)

    def page_down(self) -> None:
        n = len(self._filtered)
        if n:
            self._selected = min(n - 1, self._selected + self._max_visible)

    def fold_or_up(self) -> None:
        """Fold the current node if foldable and open; else jump to segment start upward."""
        item = self.selected_item
        if item is None or item.value is None:
            return
        val = item.value
        if self._is_foldable(val) and val not in self._folded_nodes:
            self._folded_nodes.add(val)
            self._apply_filter()
        else:
            self._selected = self._find_branch_segment_start("up")

    def unfold_or_down(self) -> None:
        """Unfold the current node if folded; else jump to segment start downward."""
        item = self.selected_item
        if item is None or item.value is None:
            return
        val = item.value
        if val in self._folded_nodes:
            self._folded_nodes.discard(val)
            self._apply_filter()
        else:
            self._selected = self._find_branch_segment_start("down")

    def cycle_filter(self) -> None:
        modes: list[FilterMode] = ["default", "no-tools", "user-only", "labeled-only", "all"]
        idx = modes.index(self._filter_mode)
        self._filter_mode = modes[(idx + 1) % len(modes)]
        self._folded_nodes.clear()
        self._apply_filter()

    def set_filter(self, mode: FilterMode) -> None:
        self._filter_mode = mode
        self._folded_nodes.clear()
        self._apply_filter()

    def toggle_filter(self, mode: FilterMode) -> None:
        self._filter_mode = "default" if self._filter_mode == mode else mode
        self._folded_nodes.clear()
        self._apply_filter()

    def set_query(self, query: str) -> None:
        if query == self._query:
            return
        self._query = query
        self._folded_nodes.clear()
        self._apply_filter()

    def toggle_label_timestamps(self) -> None:
        self._show_label_timestamps = not self._show_label_timestamps

    # ------------------------------------------------------------------
    # Label editing
    # ------------------------------------------------------------------

    def start_label_edit(self) -> None:
        item = self.selected_item
        if item is None or item.value is None:
            return
        self._label_editing = True
        self._label_editing_value = item.value
        existing = self._labels.get(item.value)
        self._label_input = existing[0] if existing else (item.label or "")

    def label_edit_key(self, event: object) -> None:
        """Route a KeyEvent into the label editing sub-mode."""
        key = getattr(event, "key", "")
        char = getattr(event, "char", None)
        ctrl = getattr(event, "ctrl", False)
        alt = getattr(event, "alt", False)
        if key == "enter":
            self.commit_label_edit()
        elif key in ("escape",) or (key == "c" and ctrl):
            self.cancel_label_edit()
        elif key == "backspace":
            self._label_input = self._label_input[:-1]
        elif char and char.isprintable() and not ctrl and not alt:
            self._label_input += char

    def commit_label_edit(self) -> None:
        if self._label_editing_value is not None:
            text = self._label_input.strip() or None
            self._set_label(self._label_editing_value, text)
        self._label_editing = False
        self._label_input = ""
        self._label_editing_value = None

    def cancel_label_edit(self) -> None:
        self._label_editing = False
        self._label_input = ""
        self._label_editing_value = None

    def update_label(self, value: T, label: str | None, timestamp: str | None = None) -> None:
        """External update (e.g. from session storage on load)."""
        self._set_label(value, label, timestamp)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_label(self, value: T, label: str | None, timestamp: str | None = None) -> None:
        if label:
            from datetime import datetime

            ts = timestamp or datetime.now(tz=UTC).isoformat()
            self._labels[value] = (label, ts)
        else:
            self._labels.pop(value, None)
        # Propagate to the all_rows entry so search_text picks up the label
        for row in self._all_rows:
            if row.value == value:
                row.label = label
                row.label_timestamp = self._labels.get(value, (None, None))[1]  # type: ignore[misc]
                break
        if self._on_label_change is not None:
            self._on_label_change(value, label)

    def _move(self, direction: int) -> None:
        n = len(self._filtered)
        if not n:
            return
        for _ in range(n):
            self._selected = (self._selected + direction) % n
            if self._filtered[self._selected].selectable:
                break

    def _build_all_children_map(self) -> dict[T, list[T]]:
        """parent_value → [child values] across ALL rows (for fold descendant exclusion)."""
        m: dict[T, list[T]] = {}
        for r in self._all_rows:
            if r.value is not None and r.parent_value is not None:
                m.setdefault(r.parent_value, []).append(r.value)
        return m

    def _build_visible_maps(self) -> None:
        """Rebuild _visible_parent_map and _visible_children_map from _filtered."""
        visible_values: set[T] = {r.value for r in self._filtered if r.value is not None}
        all_by_value: dict[T, TreeRow[T]] = {
            r.value: r for r in self._all_rows if r.value is not None
        }

        def find_visible_ancestor(val: T) -> T | None:
            row = all_by_value.get(val)
            if row is None:
                return None
            parent = row.parent_value
            while parent is not None:
                if parent in visible_values:
                    return parent
                prow = all_by_value.get(parent)
                if prow is None:
                    break
                parent = prow.parent_value
            return None

        parent_map: dict[T, T | None] = {}
        children_map: dict[T | None, list[T]] = {None: []}

        for row in self._filtered:
            if row.value is None:
                continue
            anc = find_visible_ancestor(row.value)
            parent_map[row.value] = anc
            children_map.setdefault(anc, []).append(row.value)

        self._visible_parent_map = parent_map
        self._visible_children_map = children_map

    def _apply_filter(self) -> None:
        """Rebuild _filtered from _all_rows, applying mode + folds + fuzzy query."""
        # Save current selection value for preservation
        if self._filtered and 0 <= self._selected < len(self._filtered):
            self._last_selected_value = self._filtered[self._selected].value

        rows = self._all_rows

        if self._filter_mode == "default":
            rows = [
                r
                for r in rows
                if r.role not in _TOOL_ROLES
                and r.role not in _SETTINGS_ROLES
                and not r.role.startswith("info:")
                and r.selectable
            ]
        elif self._filter_mode == "no-tools":
            rows = [r for r in rows if r.role not in _TOOL_ROLES]
        elif self._filter_mode == "user-only":
            rows = [r for r in rows if r.role == "user"]
        elif self._filter_mode == "labeled-only":
            rows = [
                r
                for r in rows
                if r.value is not None and (r.value in self._labels or r.label is not None)
            ]
        # "all": keep everything

        # Exclude descendants of folded nodes
        if self._folded_nodes:
            all_children = self._build_all_children_map()
            skip: set[T] = set()
            queue: list[T] = []
            for fid in self._folded_nodes:
                queue.extend(all_children.get(fid, []))
            while queue:
                nid = queue.pop()
                skip.add(nid)
                queue.extend(all_children.get(nid, []))
            rows = [r for r in rows if r.value not in skip]

        if self._query:
            rows = fuzzy_filter(rows, self._query, lambda r: r.search_text)

        self._filtered = rows

        # Restore selection: exact match first, then nearest visible ancestor
        if self._last_selected_value is not None and rows:
            for i, r in enumerate(rows):
                if r.value == self._last_selected_value:
                    self._selected = i
                    self._build_visible_maps()
                    return
            self._selected = self._find_nearest_visible_index(self._last_selected_value)
        else:
            self._selected = min(self._selected, max(0, len(rows) - 1)) if rows else 0

        self._build_visible_maps()

    def _find_nearest_visible_index(self, value: T) -> int:
        """Walk up the parent chain until we find a visible ancestor; fallback to last."""
        if not self._filtered:
            return 0
        visible: dict[T, int] = {
            r.value: i for i, r in enumerate(self._filtered) if r.value is not None
        }
        all_by_value: dict[T, TreeRow[T]] = {
            r.value: r for r in self._all_rows if r.value is not None
        }
        cur: T | None = value
        while cur is not None:
            if cur in visible:
                return visible[cur]
            row = all_by_value.get(cur)
            if row is None:
                break
            cur = row.parent_value
        return max(0, len(self._filtered) - 1)

    def _is_foldable(self, value: T) -> bool:
        """A node is foldable if it has visible children AND is a root or segment start."""
        children = self._visible_children_map.get(value)
        if not children:
            return False
        parent = self._visible_parent_map.get(value)
        if parent is None:
            return True  # root node is always foldable if it has children
        siblings = self._visible_children_map.get(parent)
        return siblings is not None and len(siblings) > 1

    def _find_branch_segment_start(self, direction: str) -> int:
        """Jump to the start or end of the current branch segment."""
        item = self.selected_item
        if item is None or item.value is None:
            return self._selected

        index_by_value: dict[T, int] = {
            r.value: i for i, r in enumerate(self._filtered) if r.value is not None
        }
        current = item.value

        if direction == "down":
            while True:
                children = self._visible_children_map.get(current, [])
                if not children:
                    return index_by_value.get(current, self._selected)
                if len(children) > 1:
                    return index_by_value.get(children[0], self._selected)
                current = children[0]
        else:  # up
            while True:
                parent = self._visible_parent_map.get(current)
                if parent is None:
                    return index_by_value.get(current, self._selected)
                siblings = self._visible_children_map.get(parent, [])
                if len(siblings) > 1:
                    seg = index_by_value.get(current, self._selected)
                    if seg < self._selected:
                        return seg
                current = parent

    def _scroll_start(self) -> int:
        """Center the selected row in the viewport."""
        count = len(self._filtered)
        visible = min(self._max_visible, count)
        center = self._selected - visible // 2
        return max(0, min(center, count - visible))

    def _status_label(self) -> str:
        match self._filter_mode:
            case "default":
                return ""
            case "no-tools":
                return "  [no-tools]"
            case "user-only":
                return "  [user]"
            case "labeled-only":
                return "  [labeled]"
            case "all":
                return "  [all]"
        return ""

    @staticmethod
    def _help_lines(width: int) -> list[str]:
        """Render key-hint chunks onto as many lines as needed (greedy wrap)."""
        hints = [
            "↑/↓ move",
            "←/→ fold/expand",
            "pgup/pgdn page",
            "shift+L label",
            "shift+T label time",
            "ctrl+d/t/u/l/a filters",
            "ctrl+f cycle",
            "type to search",
        ]
        sep = " · "
        indent = "  "
        out: list[str] = []
        current = ""
        for hint in hints:
            candidate = (indent + hint) if not current else (current + sep + hint)
            if not current or len(candidate) <= width:
                current = candidate
            else:
                out.append(current)
                current = indent + hint
        if current:
            out.append(current)
        return out

    @staticmethod
    def _format_label_ts(ts: str) -> str:
        """Format a label ISO timestamp (HH:MM or M/D HH:MM or YY/M/D HH:MM)."""
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts)
            now = datetime.now(tz=UTC)
            hh_mm = dt.strftime("%H:%M")
            if dt.date() == now.date():
                return hh_mm
            if dt.year == now.year:
                return f"{dt.month}/{dt.day} {hh_mm}"
            return f"{str(dt.year)[-2:]}/{dt.month}/{dt.day} {hh_mm}"
        except Exception:
            return ts

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        buf = Buffer.empty(Rect(0, 0, width, 0))
        rows = self.render_cells(Rect(0, 0, width, 0), buf)
        return [row_to_ansi(buf, row) for row in range(rows)]

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        output_row = area.y

        def write(spans: list[Span], background: Style | None = None) -> None:
            nonlocal output_row
            buf.grow_to(output_row + 1)
            buf.set_line(area.x, output_row, Line(spans), area.width)
            if background is not None:
                buf.set_style(Rect(area.x, output_row, area.width, 1), background)
            output_row += 1

        write([Span("  Session Tree", Style().bold())])
        for help_line in self._help_lines(area.width):
            write([Span(help_line, self._dim_style)])
        if self._query:
            write(
                [
                    Span("  Type to search: ", self._dim_style),
                    Span(self._query, self._accent_style),
                    Span("█", self._dim_style),
                ]
            )
        else:
            write([Span("  Type to search:", self._dim_style)])
        write([Span("─" * area.width, self._dim_style)])
        write([])

        # Label-editing sub-mode: replace tree items with input prompt
        if self._label_editing:
            write([Span("  Label (empty to remove):", self._dim_style)])
            write([Span(f"  {self._label_input}█")])
            write([Span("  enter: save  ·  esc: cancel", self._dim_style)])
            write([])
            return output_row - area.y

        items = self._filtered
        if not items:
            write([Span("  no matches", self._dim_style)])
            write([Span(f"  (0/0){self._status_label()}", self._dim_style)])
            write([])
            return output_row - area.y

        count = len(items)
        visible = min(self._max_visible, count)
        start = self._scroll_start()
        end = start + visible

        for i in range(start, end):
            row = items[i]
            is_sel = i == self._selected
            val = row.value

            spans = [Span("› ", self._accent_style) if is_sel else Span("  ")]

            # Fold marker: replace ─ in connector with ⊟ (foldable) or ⊞ (folded)
            is_folded = val in self._folded_nodes if val is not None else False
            is_foldable = self._is_foldable(val) if val is not None else False
            has_connector = "├─" in row.prefix or "└─" in row.prefix
            raw_prefix = row.prefix
            if has_connector:
                if is_folded:
                    raw_prefix = raw_prefix.replace("├─", "├⊞", 1).replace("└─", "└⊞", 1)
                elif is_foldable:
                    raw_prefix = raw_prefix.replace("├─", "├⊟", 1).replace("└─", "└⊟", 1)
            spans.append(Span(raw_prefix, self._dim_style))

            # Folded root nodes (no connector) get a ⊞ fold marker before the path marker
            if is_folded and not has_connector:
                spans.append(Span("⊞ ", self._accent_style))

            # Active path: "" for non-active nodes (no reserved space)
            if row.on_active_path:
                spans.append(Span("• ", self._accent_style))

            # Label (in-memory override takes precedence over row.label)
            stored = self._labels.get(val) if val is not None else None
            lbl_text = (stored[0] if stored else None) or row.label
            if lbl_text:
                spans.append(Span(f"[{lbl_text}] ", Style().with_fg("yellow")))
                if self._show_label_timestamps:
                    ts = (stored[1] if stored else None) or row.label_timestamp
                    if ts:
                        spans.append(Span(self._format_label_ts(ts) + " ", self._dim_style))

            # Entry content
            style = self._dim_style if not row.selectable else self._role_style(row.role, row.text)
            if row.role in ("user", "assistant"):
                spans.extend([Span(f"{row.role}: ", style), Span(row.text)])
            elif row.text:
                spans.append(Span(f"[{row.role}]: {row.text}", style))
            else:
                spans.append(Span(f"[{row.role}]", style))
            if not row.selectable:
                spans.append(Span("  (pending tool result — can't branch here)", self._dim_style))
            elif row.is_current:
                spans.append(Span("  (current)", self._dim_style))
            write(spans, self._selected_bg if is_sel else None)

        # counter then blank line
        status = self._status_label()
        if self._show_label_timestamps:
            status += "  [+label time]"
        write([Span(f"  ({self._selected + 1}/{count}){status}", self._dim_style)])
        write([])
        return output_row - area.y
