from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.session.manager import SessionManager

if TYPE_CHECKING:
    from tau.session.types import SessionInfo

    from tau.runtime.service import Runtime


def _humanize_age(dt: datetime) -> str:
    """Human-readable relative time, e.g. '2 hours ago'."""
    import arrow

    return arrow.get(dt).humanize(arrow.get(datetime.now()))


def _session_label(session: SessionInfo) -> str:
    return session.name or session.id[:12]


def _shorten_path(path: Path) -> str:
    """Abbreviate a cwd under the user's home directory, e.g. '~/code/tau'."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


class SessionSidebar:
    """Session list and switcher for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._list_container: Any | None = None

    def render(self) -> None:
        """Render the sidebar and subscribe it to session-lifecycle events."""
        cwd = self._runtime.session_manager.cwd

        with ui.column().classes("w-[260px] h-full min-h-0 gap-0 tau-sidebar"):
            with ui.column().classes("w-full gap-2 p-3 tau-sidebar-header"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Tau").classes("text-sm font-semibold text-[var(--text)]")
                    ui.button(on_click=self._new_session).props("unelevated icon=add round").style(
                        "background: var(--bg-hover) !important;"
                        " color: var(--text-muted) !important;"
                        " box-shadow: none !important;"
                    )
                ui.label(_shorten_path(cwd)).classes(
                    "w-full truncate px-2 py-1 tau-project-path"
                )

            with (
                ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"),
                ui.scroll_area().classes("w-full h-full"),
            ):
                self._list_container = ui.column().classes("w-full gap-0")

        self._refresh()

        async def on_session_start(event: object) -> None:
            del event
            self._refresh()

        unsub = self._runtime.hooks.register("session_start", on_session_start)
        ui.context.client.on_disconnect(unsub)

    async def _new_session(self) -> None:
        await self._runtime.new_session()

    def _refresh(self) -> None:
        if self._list_container is None:
            return
        cwd = self._runtime.session_manager.cwd
        current_file = self._runtime.session_manager.session_file
        sessions = SessionManager.list(cwd)

        self._list_container.clear()
        with self._list_container:
            for session in sessions:
                self._render_session_row(session, active=session.path == current_file)

    def _render_session_row(self, session: SessionInfo, *, active: bool) -> None:
        classes = "w-full h-[54px] justify-center px-3 tau-session-row" + (
            " tau-active" if active else ""
        )

        async def switch() -> None:
            if not active:
                await self._runtime.resume_session(session.path)

        with ui.column().classes(classes).on("click", switch):
            ui.label(_session_label(session)).classes("text-xs font-medium truncate text-[var(--text)]")
            with ui.row().classes("w-full gap-2 text-[11px] text-[var(--text-dim)]"):
                ui.label(_humanize_age(session.modified))
                ui.label(f"{session.message_count} msgs")
