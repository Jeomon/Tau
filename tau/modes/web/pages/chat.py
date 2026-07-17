from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from tau.modes.web.components.branch_navigator import BranchNavigatorDialog
from tau.modes.web.components.chat_minimap import ChatMinimap
from tau.modes.web.components.file_explorer import FileExplorerPanel
from tau.modes.web.components.input_section import InputSection
from tau.modes.web.components.message_list import MessageList
from tau.modes.web.components.plugins_dialog import PluginsDialog
from tau.modes.web.components.session_sidebar import SessionSidebar
from tau.modes.web.components.session_topbar import SessionTopBar
from tau.modes.web.components.settings_dialog import SettingsDialog
from tau.modes.web.components.skills_dialog import SkillsDialog

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class ChatPage:
    """Main browser chat page for one Tau runtime."""

    def __init__(self, runtime: Runtime, *, dark_mode: ui.dark_mode) -> None:
        self._runtime = runtime
        self._dark_mode = dark_mode

    def render(self) -> None:
        """Render the chat page into the current NiceGUI page context."""
        with ui.row().classes("w-full h-[100vh] gap-0"):
            file_panel = FileExplorerPanel(self._runtime)
            settings_dialog = SettingsDialog(self._runtime)
            skills_dialog = SkillsDialog()
            plugins_dialog = PluginsDialog(self._runtime)
            branch_dialog = BranchNavigatorDialog(self._runtime)
            message_list = MessageList(self._runtime)
            SessionSidebar(
                self._runtime,
                dark_mode=self._dark_mode,
                on_session_loading=message_list.show_loading,
                on_preview_session=message_list.preview_session,
                on_open_settings=settings_dialog.open,
                on_open_skills=skills_dialog.open,
                on_open_plugins=plugins_dialog.open,
                on_open_file=file_panel.open_file,
            ).render()
            with ui.column().classes("flex-1 min-w-0 h-full min-h-0 gap-0"):
                # Full-bleed, edge-to-edge like pi-web's top bar — everything
                # else stays inset in its own padded column below it.
                SessionTopBar(
                    self._runtime,
                    on_toggle_files=file_panel.toggle,
                    on_open_branches=branch_dialog.open,
                ).render()
                # w-full is required here, not just flex-1 — this column now
                # sits inside a *column*-direction wrapper (added for the
                # full-bleed top bar above), so flex-1/flex-grow governs its
                # height, not its width. Without an explicit width it falls
                # back to shrink-to-fit content instead of filling available
                # horizontal space, which is why the whole chat column (and
                # everything in it, including tool-call blocks) rendered far
                # narrower than the viewport with a large empty gap on the
                # right.
                with ui.column().classes("w-full flex-1 min-w-0 min-h-0 gap-4 px-6 pt-4 pb-2"):
                    with ui.row().classes("w-full flex-1 min-h-0 gap-1"):
                        with ui.column().classes("flex-1 min-w-0 h-full min-h-0"):
                            message_list.render()
                        ChatMinimap(self._runtime, message_list).render()
                    InputSection(self._runtime).render()
            file_panel.render()
            settings_dialog.render()
            skills_dialog.render()
            plugins_dialog.render()
            branch_dialog.render()
