from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Literal, TypeVar

from tau.tui.utils import BOLD, RESET, fuzzy_filter, truncate, visible_width

T = TypeVar("T")

ColorFn = Callable[[str], str]

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
        role_color: Callable[[str, str], ColorFn],
        accent_color: ColorFn,
        dim_color: ColorFn,
        max_visible: int = 10,
        selected_bg: ColorFn | None = None,
        on_label_change: Callable[[T, str | None], None] | None = None,
    ) -> None:
        self._all_rows: list[TreeRow[T]] = rows
        self._filtered: list[TreeRow[T]] = list(rows)
        self._role_color = role_color
        self._accent_color = accent_color
        self._dim_color = dim_color
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
    def _help_lines(dim: ColorFn, width: int) -> list[str]:
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
                out.append(dim(current))
                current = indent + hint
        if current:
            out.append(dim(current))
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
        title = f"{BOLD}  Session Tree{RESET}"

        if self._query:
            search_display = (
                self._dim_color("  Type to search: ")
                + self._accent_color(self._query)
                + self._dim_color("█")
            )
        else:
            search_display = self._dim_color("  Type to search:")

        lines: list[str] = (
            [title]
            + self._help_lines(self._dim_color, width)
            + [search_display, self._dim_color("─" * width), ""]
        )

        # Label-editing sub-mode: replace tree items with input prompt
        if self._label_editing:
            lines.append(self._dim_color("  Label (empty to remove):"))
            lines.append(f"  {self._label_input}█")
            lines.append(self._dim_color("  enter: save  ·  esc: cancel"))
            lines.append("")
            return lines

        items = self._filtered
        if not items:
            lines.append(self._dim_color("  no matches"))
            lines.append(self._dim_color(f"  (0/0){self._status_label()}"))
            lines.append("")
            return lines

        count = len(items)
        visible = min(self._max_visible, count)
        start = self._scroll_start()
        end = start + visible

        for i in range(start, end):
            row = items[i]
            is_sel = i == self._selected
            val = row.value

            cursor = self._accent_color("› ") if is_sel else "  "

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
            prefix = self._dim_color(raw_prefix)

            # Folded root nodes (no connector) get a ⊞ fold marker before the path marker
            fold_root_marker = ""
            if is_folded and not has_connector:
                fold_root_marker = self._accent_color("⊞ ")

            # Active path: "" for non-active nodes (no reserved space)
            marker = self._accent_color("• ") if row.on_active_path else ""

            # Label (in-memory override takes precedence over row.label)
            label_str = ""
            stored = self._labels.get(val) if val is not None else None
            lbl_text = (stored[0] if stored else None) or row.label
            if lbl_text:
                label_str = f"\x1b[33m[{lbl_text}]\x1b[0m "  # warning yellow
                if self._show_label_timestamps:
                    ts = (stored[1] if stored else None) or row.label_timestamp
                    if ts:
                        label_str += self._dim_color(self._format_label_ts(ts) + " ")

            # Entry content
            color = self._dim_color if not row.selectable else self._role_color(row.role, row.text)
            if row.role in ("user", "assistant"):
                content = color(f"{row.role}: ") + row.text
            elif row.text:
                content = color(f"[{row.role}]: {row.text}")
            else:
                content = color(f"[{row.role}]")
            if not row.selectable:
                content += self._dim_color("  (pending tool result — can't branch here)")
            elif row.is_current:
                content += self._dim_color("  (current)")

            line = truncate(
                cursor + prefix + fold_root_marker + marker + label_str + content, width
            )
            if is_sel and self._selected_bg is not None:
                fill = max(0, width - visible_width(line))
                line = self._selected_bg(line + " " * fill)

            lines.append(line)

        # counter then blank line
        status = self._status_label()
        if self._show_label_timestamps:
            status += "  [+label time]"
        lines.append(self._dim_color(f"  ({self._selected + 1}/{count}){status}"))
        lines.append("")

        return lines
