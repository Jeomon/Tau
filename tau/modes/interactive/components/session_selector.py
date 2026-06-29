"""Session resume selector component."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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


def _age(dt: datetime) -> str:
    """Format a datetime as a compact relative age string."""
    now = datetime.now() if dt.tzinfo is None else datetime.now(tz=UTC)
    secs = max(0, (now - dt).total_seconds())
    mins = int(secs / 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{days // 7}w"
    if days < 30:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def _shorten(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _visible_len(s: str) -> int:
    """Approximate visible terminal width of a string (strips ANSI escapes)."""
    plain = re.sub(r"\x1b\[[0-9;]*[mK]|\x1b\][^\x07]*\x07", "", s)
    return len(plain)


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

    def render(self, width: int) -> list[str]:
        t = self._theme
        divider = t.border("─" * width)
        lines: list[str] = []

        # Header bar
        scope_label = (
            f"{t.emphasis('◉ Folder')}  {t.muted('○ All')}"
            if self._scope == "current"
            else f"{t.muted('○ Folder')}  {t.emphasis('◉ All')}"
        )
        sort_label = f"{t.muted('Sort:')} {t.accent(self._SORT_LABELS[self._sort_idx])}"
        header_right = f"{scope_label}  {sort_label}"
        title_left = f"  {t.emphasis('Resume Session')}"
        right_plain_len = _visible_len(header_right)
        pad = max(0, width - _visible_len(title_left) - right_plain_len - 1)
        lines.append(title_left + " " * pad + header_right)

        # Hints
        if self._confirming_delete is not None:
            del_path = self._confirming_delete
            short = _shorten(del_path)[: width - 20]
            lines.append("  " + t.error(f"Delete '{short}'? enter=yes  esc=no"))
        elif self._status_msg:
            lines.append("  " + t.warning(self._status_msg))
        else:
            lines.append(
                "  " + t.muted("tab scope  ctrl+r sort  ctrl+d delete  type search  esc cancel")
            )

        # Search line
        if self._search:
            lines.append("  " + t.muted(f"/{self._search}█"))

        lines.append(divider)

        # Session list
        if not self._filtered:
            if self._search:
                lines.append("  " + t.muted(f"No sessions match '{self._search}'"))
            elif self._scope == "current":
                lines.append("  " + t.muted("No sessions in current folder — press Tab for all"))
            else:
                lines.append("  " + t.muted("No sessions found"))
        else:
            count = len(self._filtered)
            visible = min(self._max_visible, count)
            start = max(0, min(self._selected - visible // 2, count - visible))

            if start > 0:
                lines.append("  " + t.muted(f"↑ {start} more"))

            show_cwd = self._scope == "all"

            for i in range(start, min(start + visible, count)):
                session = self._filtered[i]
                is_sel = i == self._selected
                sel_path = (
                    Path(session.path) if not isinstance(session.path, Path) else session.path
                )
                is_del_target = sel_path == self._confirming_delete

                display = session.name or session.id[:20]
                age_str = _age(session.modified)
                count_str = str(getattr(session, "message_count", 0))

                right_parts = [count_str, age_str]
                if show_cwd and hasattr(session, "cwd") and session.cwd:
                    right_parts.insert(0, _shorten(Path(session.cwd)))
                right = "  ".join(right_parts)

                cursor = "→ " if is_sel else "  "
                right_len = _visible_len(right)
                available = width - 4 - right_len - 2
                msg = display[: max(8, available)]

                if is_del_target:
                    msg_styled = t.error(msg)
                    right_styled = t.error(right)
                elif is_sel:
                    msg_styled = t.emphasis(msg)
                    right_styled = t.muted(right)
                elif session.name:
                    msg_styled = t.warning(msg)
                    right_styled = t.muted(right)
                else:
                    msg_styled = msg
                    right_styled = t.muted(right)

                cursor_styled = t.emphasis(cursor) if is_sel else cursor
                left = cursor_styled + msg_styled
                spacing = max(1, width - _visible_len(left) - right_len)
                lines.append(left + " " * spacing + right_styled)

            remaining = count - (start + visible)
            if remaining > 0:
                lines.append("  " + t.muted(f"↓ {remaining} more"))

        lines.append(divider)
        return lines

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
