from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.modes.interactive.components.session_selector import _cleanup_session_media
from tau.modes.web.components.file_explorer import _build_tree
from tau.modes.web.components.worktree_menu import WorktreeMenu
from tau.session.manager import SessionManager
from tau.settings.paths import get_app_version

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
        on_session_loading: Callable[[], None] | None = None,
        on_preview_session: Callable[[Path], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_open_skills: Callable[[], None] | None = None,
        on_open_plugins: Callable[[], None] | None = None,
        on_open_file: Callable[[Path], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._on_session_loading = on_session_loading
        self._on_preview_session = on_preview_session
        self._on_open_settings = on_open_settings
        self._on_open_skills = on_open_skills
        self._on_open_plugins = on_open_plugins
        self._on_open_file = on_open_file
        self._list_container: Any | None = None
        self._explorer_container: Any | None = None
        self._outer: Any | None = None
        self._visible = True
        self._filter_text = ""
        self._confirming_delete: Path | None = None
        self._renaming_path: Path | None = None
        self._pending_session_path: Path | None = None

    def toggle(self) -> bool:
        """Show or hide the sidebar, sliding its width in/out (pi-web's
        sidebar-container spec: 260px <-> 0, inner content held at a fixed
        260px so it doesn't reflow/wrap mid-animation). Returns the new
        visibility so callers (the top bar's toggle icon) can mirror it."""
        self._visible = not self._visible
        if self._outer is not None:
            width = "260px" if self._visible else "0px"
            border = "1px solid var(--border)" if self._visible else "none"
            self._outer.style(f"width: {width}; min-width: {width}; border-right: {border};")
        return self._visible

    def render(self) -> None:
        """Render the sidebar and subscribe it to session-lifecycle events."""
        with ui.column().classes("h-full min-h-0 gap-0 overflow-hidden tau-sidebar-outer").style(
            "width: 260px; min-width: 260px; border-right: 1px solid var(--border);"
        ) as outer:
            self._outer = outer
            with ui.column().classes("w-[260px] min-w-[260px] h-full min-h-0 gap-0 tau-sidebar"):
                self._render_content()

        self._refresh()

        async def on_session_start(event: object) -> None:
            del event
            self._pending_session_path = None
            self._refresh()

        unsub = self._runtime.hooks.register("session_start", on_session_start)
        ui.context.client.on_disconnect(unsub)

    def _render_content(self) -> None:
        with ui.column().classes("w-full gap-2 p-3 tau-sidebar-header"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-baseline gap-1.5"):
                    ui.label("Tau").classes("text-lg font-semibold text-[var(--text)]")
                    ui.label(f"v{get_app_version()}").classes("text-xs text-[var(--text-dim)]")
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
            self._list_container = ui.column().classes("w-full min-w-0 gap-0")

        if self._on_open_file is not None:
            # Always-visible collapsible file tree docked in the sidebar,
            # matching pi-web's layout (an "EXPLORER" section under the
            # session list) instead of tau's original slide-out right
            # panel — the panel itself still exists and still opens when
            # a file is picked here, so tabs/preview logic isn't duplicated.
            # Hand-rolled header (not ui.expansion's built-in label) since
            # pi-web's header also carries a refresh icon button next to
            # the title, and ui.expansion only supports a plain string.
            expanded = [True]
            body_container: dict[str, Any] = {}
            chevron_ref: dict[str, Any] = {}

            def toggle_explorer() -> None:
                expanded[0] = not expanded[0]
                body_container["el"].set_visibility(expanded[0])
                chevron_ref["el"].classes(toggle="tau-explorer-chevron-open")

            with ui.column().classes("w-full gap-0 tau-sidebar-explorer"):
                with ui.row().classes(
                    "w-full items-center gap-1 px-3 py-1.5 cursor-pointer tau-explorer-header"
                ).on("click", toggle_explorer):
                    chevron_ref["el"] = ui.icon("expand_more").classes(
                        "tau-explorer-chevron tau-explorer-chevron-open"
                    )
                    ui.label("EXPLORER").classes("flex-1 text-[11px] font-medium tau-explorer-title")
                    refresh_btn = ui.icon("refresh").classes("tau-explorer-refresh")
                    refresh_btn.on("click.stop", self._refresh_explorer)
                body = ui.scroll_area().classes("w-full h-[220px] tau-sidebar-scroll")
                with body:
                    self._explorer_container = ui.column().classes(
                        "w-full min-w-0 items-stretch gap-0 p-1"
                    )
                body_container["el"] = body
            self._refresh_explorer()

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

    async def _new_session(self) -> None:
        await self._runtime.new_session()

    def _on_filter_change(self, event: Any) -> None:
        self._filter_text = str(event.value or "").strip().lower()
        self._refresh()

    def _refresh_explorer(self) -> None:
        if self._explorer_container is None:
            return
        self._explorer_container.clear()
        with self._explorer_container:
            nodes = _build_tree(self._runtime.session_manager.cwd)
            ui.tree(
                nodes, node_key="id", label_key="label", on_select=self._on_explorer_select
            ).classes("w-full text-xs")

    def _on_explorer_select(self, event: Any) -> None:
        node_id = getattr(event, "value", None)
        if not node_id or self._on_open_file is None:
            return
        path = Path(node_id)
        if path.is_file():
            self._on_open_file(path)

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
        # Matches pi-web's SessionSidebar.tsx session row exactly: fixed
        # 54px height, flat full-bleed background (no border-radius), a 2px
        # left accent border when selected, 14px/8px left/right padding.
        classes = "w-full flex-nowrap items-center h-[54px] pl-[14px] pr-2 gap-1.5 tau-session-row" + (
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
            with ui.column().classes("flex-1 min-w-0 gap-0"):
                ui.label(_session_label(session)).classes(
                    "w-full min-w-0 truncate text-xs text-[var(--text)]"
                    + (" font-medium" if active else "")
                )
                with ui.row().classes(
                    "w-full flex-nowrap gap-2 mt-0.5 text-[11px] text-[var(--text-dim)]"
                ):
                    ui.label(_humanize_age(session.modified)).classes("flex-shrink-0")
                    ui.label(f"{session.message_count} msgs").classes("flex-shrink-0")
            # Boxed, bordered 32x32 icon buttons matching pi-web's action
            # buttons — not the plain borderless icon-only look used for the
            # footer controls elsewhere in the app.
            # color=None is the real fix for these rendering in Quasar's
            # accent blue regardless of CSS — ui.button() defaults
            # color='primary' as an actual Quasar prop, not just a class.
            rename_btn = (
                ui.button(icon="edit", color=None)
                .props("flat dense")
                .classes("tau-session-action-btn")
            )
            rename_btn.on("click.stop", lambda: self._start_rename(session.path))
            if not active:
                delete_btn = (
                    ui.button(icon="delete_outline", color=None)
                    .props("flat dense")
                    .classes("tau-session-action-btn tau-session-delete-btn")
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
