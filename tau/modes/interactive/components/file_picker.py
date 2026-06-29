from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tau.tui.component import Component
from tau.tui.input import InputEvent, Key, KeyEvent
from tau.tui.utils import fuzzy_filter

if TYPE_CHECKING:
    from tau.tui.theme import SelectListTheme

VISIBLE_ROWS = 6


@dataclass
class FileEntry:
    name: str
    is_dir: bool
    path: Path


class FilePicker(Component):
    """
    Fuzzy-filtered file browser shown above the input when the user types '@'.
    Navigates the filesystem level by level.

    Tab / Enter on a dir  → descend into it (returns None from enter_selected).
    Tab / Enter on a file → select it (returns the FileEntry).
    Backspace on empty query in the parent layout calls go_up().
    """

    def __init__(self, cwd: Path | None = None, theme: SelectListTheme | None = None) -> None:
        self._root = cwd or Path.cwd()
        self._cwd = self._root
        self._all_entries: list[FileEntry] = []
        self._entries: list[FileEntry] = []
        self._selected = 0
        self._query = ""
        self._active = False

        from tau.tui.theme import SelectListTheme as _ST

        self._theme = theme or _ST()

    def set_theme(self, theme: SelectListTheme) -> None:
        self._theme = theme

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def selected(self) -> FileEntry | None:
        return self._entries[self._selected] if self._entries else None

    @property
    def line_count(self) -> int:
        breadcrumb = 1 if self._cwd != self._root else 0
        return breadcrumb + min(VISIBLE_ROWS, len(self._entries))

    @property
    def cwd_relative_path(self) -> str:
        """Relative path of the current browsed dir from the root."""
        try:
            return str(self._cwd.relative_to(self._root))
        except ValueError:
            return str(self._cwd)

    def open(self, cwd: Path | None = None) -> None:
        if cwd is not None:
            self._root = cwd
            self._cwd = cwd
        self._active = True
        self._query = ""
        self._selected = 0
        self._refresh_entries()

    def close(self) -> None:
        self._active = False
        self._cwd = self._root
        self._query = ""

    def set_query(self, query: str) -> None:
        """
        query is the text after '@'.  If it contains '/', treat the leading
        portion as a directory path to navigate into and the trailing portion
        as the fuzzy filter string.
        """
        if query == self._query:
            return
        self._query = query

        if "/" in query:
            dir_part, name_part = query.rsplit("/", 1)
            candidate = self._root / dir_part
            if candidate.is_dir():
                if candidate != self._cwd:
                    self._cwd = candidate
                    self._refresh_entries()
                else:
                    self._apply_filter(name_part)
                return

        self._refresh_entries()
        self._apply_filter(query)

    def move_up(self) -> None:
        if self._entries:
            self._selected = (self._selected - 1) % len(self._entries)

    def move_down(self) -> None:
        if self._entries:
            self._selected = (self._selected + 1) % len(self._entries)

    def enter_selected(self) -> FileEntry | None:
        """
        Descend into the selected dir (returns None) or return the selected
        file entry so the caller can insert its path.
        """
        entry = self.selected
        if entry is None:
            return None
        if entry.is_dir:
            self._cwd = entry.path
            self._query = ""
            self._selected = 0
            self._refresh_entries()
            return None
        return entry

    def go_up(self) -> bool:
        """Ascend one directory.  Returns False if already at root."""
        if self._cwd == self._root:
            return False
        self._cwd = self._cwd.parent
        self._query = ""
        self._selected = 0
        self._refresh_entries()
        return True

    def relative_path(self, entry: FileEntry) -> str:
        try:
            return str(entry.path.relative_to(self._root))
        except ValueError:
            return str(entry.path)

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        if not self._active:
            return []

        t = self._theme
        lines: list[str] = []

        if self._cwd != self._root:
            try:
                rel = str(self._cwd.relative_to(self._root))
            except ValueError:
                rel = str(self._cwd)
            lines.append(t.indicator(f"  @ {rel}/"))

        if not self._entries:
            lines.append(t.empty("  (no matches)"))
            return lines

        count = len(self._entries)
        visible = min(VISIBLE_ROWS, count)
        start = max(0, min(self._selected - visible + 1, count - visible))

        label_w = max(
            8,
            min(
                max(
                    len(e.name + ("/" if e.is_dir else ""))
                    for e in self._entries[start : start + visible]
                ),
                30,
            ),
        )

        if start > 0:
            lines.append(t.indicator(f"  ↑ {start} more"))

        for i in range(start, start + visible):
            entry = self._entries[i]
            is_sel = i == self._selected
            label = (entry.name + ("/" if entry.is_dir else ""))[:label_w].ljust(label_w)

            if is_sel:
                style = t.selected_dir if entry.is_dir else t.selected_label
                row = "  " + style(label)
            else:
                row = "  " + t.normal_label(label)

            lines.append(row)

        remaining = count - (start + visible)
        if remaining > 0:
            lines.append(t.indicator(f"  ↓ {remaining} more"))

        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not isinstance(event, KeyEvent):
            return False
        if event.matches(Key.UP, Key.ctrl("p")):
            self.move_up()
            return True
        if event.matches(Key.DOWN, Key.ctrl("n")):
            self.move_down()
            return True
        return False

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _refresh_entries(self) -> None:
        try:
            raw = sorted(
                self._cwd.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            raw = []
        self._all_entries = [
            FileEntry(name=p.name, is_dir=p.is_dir(), path=p)
            for p in raw
            if not p.name.startswith(".")
        ]
        name_query = self._query.rsplit("/", 1)[-1] if "/" in self._query else self._query
        self._apply_filter(name_query)

    def _apply_filter(self, name_query: str) -> None:
        q = name_query.strip()
        if not q:
            self._entries = list(self._all_entries)
        else:
            self._entries = fuzzy_filter(self._all_entries, q, lambda e: e.name)
        self._selected = min(self._selected, len(self._entries) - 1) if self._entries else 0
