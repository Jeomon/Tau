from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.modes.interactive.components.session_selector import _cleanup_session_media
from tau.modes.web.components.worktree_menu import WorktreeMenu
from tau.session.manager import SessionManager

if TYPE_CHECKING:
    from tau.runtime.service import Runtime
    from tau.session.types import SessionInfo


def _humanize_age(dt: datetime) -> str:
    """Human-readable relative time, e.g. '2 hours ago'."""
    import arrow

    return arrow.get(dt).humanize(arrow.get(datetime.now()))


def _first_message_snippet(path: Path, max_chars: int = 50) -> str | None:
    """Best-effort peek at the first user message's text, for a session-list fallback title.

    Bounded to the first ~60 lines so this stays cheap even though it's called
    per row on every sidebar refresh — the first user turn always lands there,
    right after the header and a handful of bookkeeping entries.
    """
    import json

    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(60):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                message = obj.get("message")
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                for block in message.get("contents", []):
                    text = block.get("content") if block.get("type") == "text" else None
                    if text:
                        text = " ".join(text.split())
                        return text[:max_chars] + ("…" if len(text) > max_chars else "")
    except OSError:
        pass
    return None


def _session_label(session: SessionInfo) -> str:
    return session.name or _first_message_snippet(session.path) or session.id[:12]


class SessionSidebar:
    """Session list and switcher for the browser chat page."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        dark_mode: ui.dark_mode,
        on_session_loading: Callable[[], None] | None = None,
        on_preview_session: Callable[[Path], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_open_skills: Callable[[], None] | None = None,
        on_open_plugins: Callable[[], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._dark_mode = dark_mode
        self._on_session_loading = on_session_loading
        self._on_preview_session = on_preview_session
        self._on_open_settings = on_open_settings
        self._on_open_skills = on_open_skills
        self._on_open_plugins = on_open_plugins
        self._list_container: Any | None = None
        self._theme_button: Any | None = None
        self._filter_text = ""
        self._confirming_delete: Path | None = None
        self._renaming_path: Path | None = None
        self._pending_session_path: Path | None = None

    def render(self) -> None:
        """Render the sidebar and subscribe it to session-lifecycle events."""
        with ui.column().classes("w-[260px] h-full min-h-0 gap-0 tau-sidebar"):
            with ui.column().classes("w-full gap-2 p-3 tau-sidebar-header"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Tau").classes("text-sm font-semibold text-[var(--text)]")
                    with ui.row().classes("items-center gap-1"):
                        self._theme_button = (
                            ui.button(on_click=self._toggle_theme)
                            .props(f"unelevated round dense icon={self._theme_icon()}")
                            .classes("tau-icon-btn-32")
                            .style(
                                "background: var(--bg-hover) !important;"
                                " color: var(--text-muted) !important;"
                                " box-shadow: none !important;"
                            )
                        )
                        ui.button(on_click=self._new_session).props(
                            "unelevated icon=add round dense"
                        ).classes("tau-icon-btn-32").style(
                            "background: var(--bg-hover) !important;"
                            " color: var(--text-muted) !important;"
                            " box-shadow: none !important;"
                        )
                WorktreeMenu(self._runtime).render()

            with ui.column().classes("w-full gap-1 px-3 py-2 tau-session-search-wrap"):
                search_box = (
                    ui.input(placeholder="Search sessions")
                    .props("borderless dense clearable append-icon=search")
                    .classes("w-full tau-session-search text-[var(--text)]")
                )
                search_box.on_value_change(self._on_filter_change)

            with (
                ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"),
                ui.scroll_area().classes("w-full h-full tau-sidebar-scroll"),
            ):
                self._list_container = ui.column().classes("w-full min-w-0 gap-1 px-1 py-1")

            with ui.row().classes("w-full gap-1 p-2 tau-sidebar-footer"):
                if self._on_open_settings is not None:
                    ui.button("Models", icon="settings", on_click=self._on_open_settings).props(
                        "flat no-caps dense"
                    ).classes("flex-1 tau-sidebar-footer-tab").style(
                        "color: var(--text-muted) !important;"
                    )
                if self._on_open_skills is not None:
                    ui.button("Skills", icon="auto_awesome", on_click=self._on_open_skills).props(
                        "flat no-caps dense"
                    ).classes("flex-1 tau-sidebar-footer-tab").style(
                        "color: var(--text-muted) !important;"
                    )
                if self._on_open_plugins is not None:
                    ui.button("Plugins", icon="extension", on_click=self._on_open_plugins).props(
                        "flat no-caps dense"
                    ).classes("flex-1 tau-sidebar-footer-tab").style(
                        "color: var(--text-muted) !important;"
                    )

        self._refresh()

        async def on_session_start(event: object) -> None:
            del event
            self._pending_session_path = None
            self._refresh()

        unsub = self._runtime.hooks.register("session_start", on_session_start)
        ui.context.client.on_disconnect(unsub)

    async def _new_session(self) -> None:
        await self._runtime.new_session()

    def _theme_icon(self) -> str:
        return "dark_mode" if self._dark_mode.value else "light_mode"

    def _toggle_theme(self) -> None:
        self._dark_mode.value = not self._dark_mode.value
        if self._theme_button is not None:
            self._theme_button.props(f"icon={self._theme_icon()}")

    def _on_filter_change(self, event: Any) -> None:
        self._filter_text = str(event.value or "").strip().lower()
        self._refresh()

    def _refresh(self) -> None:
        if self._list_container is None:
            return
        cwd = self._runtime.session_manager.cwd
        current_file = self._pending_session_path or self._runtime.session_manager.session_file
        sessions = SessionManager.list(cwd)
        if self._filter_text:
            sessions = [s for s in sessions if self._filter_text in _session_label(s).lower()]

        self._list_container.clear()
        with self._list_container:
            for session in sessions:
                self._render_session_row(session, active=session.path == current_file)

    def _render_session_row(self, session: SessionInfo, *, active: bool) -> None:
        # `justify-center` only centers along a row's horizontal axis, not
        # vertical, and with no py-* the content had no real top/bottom
        # breathing room — `items-center` is the correct cross-axis (vertical)
        # centering for a ui.row(), backed by explicit py-2 as a floor.
        classes = "w-full flex-nowrap items-center min-h-[48px] px-2 py-2 tau-session-row" + (
            " tau-active" if active else ""
        )

        async def switch() -> None:
            if not active:
                self._pending_session_path = session.path
                self._refresh()
                if self._on_session_loading is not None:
                    self._on_session_loading()
                await asyncio.sleep(0.01)
                if self._on_preview_session is not None:
                    self._on_preview_session(session.path)
                await self._runtime.resume_session(session.path)

        if self._confirming_delete == session.path:
            with ui.row().classes(f"{classes} items-center gap-2"):
                ui.label(f'Delete "{_session_label(session)}"?').classes(
                    "flex-1 min-w-0 truncate text-xs text-[var(--text)]"
                )
                ui.button("Delete", on_click=lambda: self._confirm_delete(session.path)).props(
                    "unelevated dense"
                ).style(
                    "background: #ef4444 !important; color: #fff !important;"
                    " box-shadow: none !important;"
                )
                ui.button("Cancel", on_click=self._cancel_delete).props("flat dense").style(
                    "color: var(--text-muted) !important;"
                )
            return

        if self._renaming_path == session.path:
            with ui.row().classes(f"{classes} items-center gap-2"):
                name_input = (
                    ui.input(value=_session_label(session))
                    .props("dense outlined autofocus")
                    .classes("flex-1 min-w-0 text-xs")
                )
                name_input.on(
                    "keydown.enter",
                    lambda: self._confirm_rename_input(session, name_input),
                )
                name_input.on("keydown.escape", self._cancel_rename)
                ui.button(
                    icon="check", on_click=lambda: self._confirm_rename_input(session, name_input)
                ).props("flat dense round size=sm").style("color: #16a34a !important;")
                ui.button(icon="close", on_click=self._cancel_rename).props(
                    "flat dense round size=sm"
                ).style("color: var(--text-muted) !important;")
            return

        with ui.row().classes(classes).on("click", switch):
            with ui.column().classes("flex-1 min-w-0 items-stretch gap-0"):
                ui.label(_session_label(session)).classes(
                    "w-full min-w-0 truncate text-xs font-medium text-[var(--text)]"
                )
                with ui.row().classes(
                    "w-full flex-nowrap gap-2 text-[11px] text-[var(--text-dim)]"
                ):
                    ui.label(_humanize_age(session.modified)).classes("flex-shrink-0")
                    ui.label(f"{session.message_count} msgs").classes("flex-shrink-0")
            rename_btn = (
                ui.button(icon="edit")
                .props("flat dense round size=sm")
                .classes("tau-session-delete-btn")
            )
            rename_btn.on("click.stop", lambda: self._start_rename(session.path))
            if not active:
                delete_btn = (
                    ui.button(icon="delete_outline")
                    .props("flat dense round size=sm")
                    .classes("tau-session-delete-btn")
                )
                delete_btn.on("click.stop", lambda: self._start_delete(session.path))

    def _start_delete(self, path: Path) -> None:
        self._confirming_delete = path
        self._refresh()

    def _cancel_delete(self) -> None:
        self._confirming_delete = None
        self._refresh()

    def _confirm_delete(self, path: Path) -> None:
        self._confirming_delete = None
        _cleanup_session_media(path)
        path.unlink(missing_ok=True)
        self._refresh()

    def _start_rename(self, path: Path) -> None:
        self._renaming_path = path
        self._refresh()

    def _cancel_rename(self) -> None:
        self._renaming_path = None
        self._refresh()

    def _confirm_rename_input(self, session: SessionInfo, name_input: Any) -> None:
        self._confirm_rename(session, str(name_input.value or ""))

    def _confirm_rename(self, session: SessionInfo, new_name: str) -> None:
        self._renaming_path = None
        new_name = new_name.strip()
        if new_name and new_name != _session_label(session):
            sm = SessionManager(session.cwd, session_file=session.path, persist=True)
            sm.append_session_info(new_name)
        self._refresh()
