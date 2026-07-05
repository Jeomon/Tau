"""Session resume selector component."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.text import Line, Span

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

_MEDIA_UUID_PATTERN = re.compile(r"\[(?:image|audio|video):([^\]]+)\]")


def _cleanup_session_media(session_path: Path) -> None:
    """Delete media files that were only referenced by the given session file.

    Scans the deleted session for [image/audio/video:{uuid}] markers, then checks
    all sibling sessions in the same project dir. Any UUID not referenced elsewhere
    has its media file removed from session_dir/media/.
    """
    session_dir = session_path.parent
    media_dir = session_dir / "media"
    if not media_dir.is_dir():
        return

    deleted_uuids: set[str] = set()
    try:
        for line in session_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for m in _MEDIA_UUID_PATTERN.finditer(line):
                deleted_uuids.add(m.group(1))
    except OSError:
        return

    if not deleted_uuids:
        return

    live_uuids: set[str] = set()
    for sibling in session_dir.glob("*.jsonl"):
        if sibling == session_path:
            continue
        try:
            for line in sibling.read_text(encoding="utf-8", errors="replace").splitlines():
                for m in _MEDIA_UUID_PATTERN.finditer(line):
                    live_uuids.add(m.group(1))
        except OSError:
            pass

    for uid in deleted_uuids - live_uuids:
        for media_file in media_dir.glob(f"{uid}.*"):
            with contextlib.suppress(OSError):
                media_file.unlink(missing_ok=True)


def _humanize_age(dt: datetime) -> str:
    """Human-readable relative time, e.g. '2 hours ago', 'a day ago'."""
    import arrow

    local_now = arrow.get(datetime.now())
    return arrow.get(dt).humanize(local_now)


def _file_size(path: Path) -> str:
    """Human-readable file size for a session file."""
    try:
        size = path.stat().st_size
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size // 1024}K"
        return f"{size / (1024 * 1024):.1f}M"
    except OSError:
        return ""


def _shorten(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


class ResumeSelector:
    """Session resume selector.

    - Up/Down    navigate
    - Enter      select session
    - Tab        toggle scope (current folder ↔ all)
    - Ctrl+R     cycle sort (date desc → date asc → name)
    - Ctrl+D     start delete-confirmation
    - Enter/Esc  confirm/cancel delete
    - Type       search by name / id
    - Backspace  delete last search char
    - Escape     cancel (when not in delete-confirmation)
    """

    _SORT_LABELS = ["Recent", "Oldest", "Name"]

    def __init__(
        self,
        current_sessions: list,
        all_sessions_loader: Callable[[], list],
        current_session_path: Path | None = None,
        max_visible: int = 10,
        theme: LayoutTheme | None = None,
    ) -> None:
        self._current = list(current_sessions)
        self._all_loader = all_sessions_loader
        self._all: list | None = None
        self._cur_path = current_session_path
        self._max_visible = max_visible

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._theme = theme

        self._scope = "current"  # "current" | "all"
        self._sort_idx = 0  # index into _SORT_LABELS
        self._search = ""
        self._filtered: list = []
        self._selected = 0

        self._confirming_delete: Path | None = None
        self._status_msg: str = ""
        self._meta_cache: dict[str, str] = {}

        self._refilter()

    # ── Public state ──────────────────────────────────────────────────────────

    @property
    def confirming_delete(self) -> bool:
        return self._confirming_delete is not None

    def selected_path(self) -> Path | None:
        if not self._filtered:
            return None
        s = self._filtered[self._selected]
        return Path(s.path) if not isinstance(s.path, Path) else s.path

    # ── Navigation ────────────────────────────────────────────────────────────

    def move_up(self) -> None:
        if self._confirming_delete is None and self._filtered:
            self._selected = max(0, self._selected - 1)
            self._status_msg = ""

    def move_down(self) -> None:
        if self._confirming_delete is None and self._filtered:
            self._selected = min(len(self._filtered) - 1, self._selected + 1)
            self._status_msg = ""

    def toggle_scope(self) -> None:
        if self._confirming_delete is not None:
            return
        if self._scope == "current":
            self._scope = "all"
            if self._all is None:
                try:
                    self._all = list(self._all_loader())
                except Exception:
                    self._all = []
        else:
            self._scope = "current"
        self._selected = 0
        self._refilter()

    def cycle_sort(self) -> None:
        if self._confirming_delete is not None:
            return
        self._sort_idx = (self._sort_idx + 1) % len(self._SORT_LABELS)
        self._refilter()

    def start_delete(self) -> None:
        if not self._filtered:
            return
        sel = self._filtered[self._selected]
        sel_path = Path(sel.path) if not isinstance(sel.path, Path) else sel.path
        if self._cur_path and sel_path == self._cur_path:
            self._status_msg = "Cannot delete the active session"
            return
        self._confirming_delete = sel_path

    def confirm_delete(self) -> None:
        path = self._confirming_delete
        self._confirming_delete = None
        if path is None:
            return
        try:
            _cleanup_session_media(path)
            path.unlink(missing_ok=True)
            self._current = [s for s in self._current if Path(s.path) != path]
            if self._all is not None:
                self._all = [s for s in self._all if Path(s.path) != path]
            self._refilter()
            self._selected = min(self._selected, max(0, len(self._filtered) - 1))
            self._status_msg = "Session deleted"
        except Exception as exc:
            self._status_msg = f"Delete failed: {exc}"

    def cancel_delete(self) -> None:
        self._confirming_delete = None

    # ── Search ────────────────────────────────────────────────────────────────

    def append_search(self, ch: str) -> None:
        if self._confirming_delete is not None:
            return
        self._search += ch
        self._selected = 0
        self._refilter()

    def backspace_search(self) -> None:
        if self._confirming_delete is not None:
            return
        if self._search:
            self._search = self._search[:-1]
            self._selected = 0
            self._refilter()

    # ── Render ────────────────────────────────────────────────────────────────

    def _session_meta(self, session) -> str:
        """Return file_size for a session, cached per session id."""
        sid = session.id
        if sid not in self._meta_cache:
            path = session.path
            session_path = path if isinstance(path, Path) else Path(path)
            size = _file_size(session_path)
            self._meta_cache[sid] = size
        return self._meta_cache[sid]

    def render_cells(self, area: Rect, buf: Buffer) -> int:
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
            text("─" * area.width, t.border)

        # ── Scope tab bar ──────────────────────────────────────────────────────
        write(
            [
                Span("  "),
                Span(
                    "[Folder]" if self._scope == "current" else "Folder",
                    t.emphasis if self._scope == "current" else t.muted,
                ),
                Span("  "),
                Span(
                    "[All]" if self._scope == "all" else "All",
                    t.emphasis if self._scope == "all" else t.muted,
                ),
                Span("    "),
                Span(f"Sort: {self._SORT_LABELS[self._sort_idx]}", t.muted),
            ]
        )
        divider()

        # ── Search box ─────────────────────────────────────────────────────────
        if self._search:
            write([Span("  "), Span("⊘", t.muted), Span(f" {self._search}█")])
        else:
            text("⊘ Search sessions…", t.muted, "  ")
        divider()

        # ── Delete confirmation ────────────────────────────────────────────────
        if self._confirming_delete is not None:
            del_path = self._confirming_delete
            short = _shorten(del_path)[: area.width - 20]
            text(f"Delete '{short}'?  Enter: yes  ·  Esc: no", t.error, "  ")
            divider()

        # ── Session list (two-line entries) ────────────────────────────────────
        show_project = self._scope == "all"

        if not self._filtered:
            if self._search:
                text(f"No sessions match '{self._search}'", t.muted, "  ")
            elif self._scope == "current":
                text("No sessions in current folder — Tab for all", t.muted, "  ")
            else:
                text("No sessions found", t.muted, "  ")
        else:
            from tau.tui.widgets.list import List, ListItem, ListState

            count = len(self._filtered)
            visible = min(self._max_visible, count)
            start = max(0, min(self._selected - visible // 2, count - visible))

            if start > 0:
                text(f"↑ {start} more above", t.muted, "  ")

            end_idx = min(start + visible, count)

            # Each session is 2 content rows (name, meta) plus a blank
            # separator between entries — never after the last one in the
            # window. Built as a flat run of ListItems (one row per
            # ListItem, matching ratatui's List); only "name" rows are ever
            # the selected one, so state.selected always points at a
            # 3k-th item within this windowed slice.
            list_items: list[ListItem] = []
            for i in range(start, end_idx):
                session = self._filtered[i]
                is_sel = i == self._selected
                sel_path = (
                    Path(session.path) if not isinstance(session.path, Path) else session.path
                )
                is_del_target = sel_path == self._confirming_delete

                # Named sessions show the name; unnamed show a short ID prefix
                display = (
                    session.name[: max(12, area.width - 6)] if session.name else session.id[:12]
                )

                size = self._session_meta(session)

                # ── Line 1: indicator + session name ──────────────────────────
                if is_del_target:
                    name_style = t.error
                    indicator_spans = [Span("> ", t.error)]
                elif is_sel:
                    name_style = t.emphasis
                    indicator_spans = [Span("> ", t.accent)]
                elif session.name:
                    name_style = t.warning
                    indicator_spans = [Span("  ", Style())]
                else:
                    name_style = t.muted
                    indicator_spans = [Span("  ", Style())]

                list_items.append(
                    ListItem(
                        Line([Span("  ", Style()), *indicator_spans, Span(display, name_style)])
                    )
                )

                # ── Line 2: age · project · size · ⚙ N ───────────────────────
                meta_parts: list[str] = [_humanize_age(session.modified)]
                if show_project and hasattr(session, "cwd") and session.cwd:
                    meta_parts.append(Path(session.cwd).name)
                if size:
                    meta_parts.append(size)
                mc = getattr(session, "message_count", 0)
                if mc > 0:
                    meta_parts.append(f"⚙ {mc}")

                meta_line = "  ·  ".join(meta_parts)
                list_items.append(ListItem(Line([Span("    ", Style()), Span(meta_line, t.muted)])))

                # blank line between entries for readability
                if i < end_idx - 1:
                    list_items.append(ListItem(Line([])))

            state = ListState()
            state.select((self._selected - start) * 3 if list_items else None)
            state.offset = 0
            buf.grow_to(row + len(list_items))
            List(items=list_items, highlight_symbol="", highlight_style=Style()).render(
                Rect(area.x, row, area.width, len(list_items)), buf, state
            )
            row += len(list_items)

            remaining = count - (start + visible)
            if remaining > 0:
                text(f"↓ {remaining} more below", t.muted, "  ")

        divider()

        # ── Status bar ─────────────────────────────────────────────────────────
        if self._status_msg:
            text(self._status_msg, t.warning, "  ")
        else:
            text(
                "tab: scope  ·  ctrl+r: sort  ·  ctrl+d: delete  ·  Esc: cancel",
                t.muted,
                "  ",
            )
        return row - area.y

    # ── Internal ──────────────────────────────────────────────────────────────

    def _active_sessions(self) -> list:
        if self._scope == "all" and self._all is not None:
            return self._all
        return self._current

    def _refilter(self) -> None:
        sessions = self._active_sessions()
        q = self._search.lower()

        if q:
            filtered = [
                s
                for s in sessions
                if q in (s.name or "").lower()
                or q in s.id.lower()
                or q in str(getattr(s, "cwd", "")).lower()
            ]
        else:
            filtered = list(sessions)

        if self._cur_path:
            filtered = [
                s
                for s in filtered
                if (Path(s.path) if not isinstance(s.path, Path) else s.path) != self._cur_path
            ]

        label = self._SORT_LABELS[self._sort_idx]
        if label == "Recent":
            filtered.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        elif label == "Oldest":
            filtered.sort(key=lambda s: s.modified.timestamp())
        elif label == "Name":
            filtered.sort(key=lambda s: (s.name or s.id).lower())

        self._filtered = filtered
        self._selected = min(self._selected, max(0, len(filtered) - 1))
