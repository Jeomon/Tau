from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from tau.modes.web.components.file_explorer import FileExplorerPanel
from tau.modes.web.components.input_section import InputSection
from tau.modes.web.components.message_list import MessageList
from tau.modes.web.components.session_sidebar import SessionSidebar
from tau.modes.web.components.session_topbar import SessionTopBar

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
            SessionSidebar(self._runtime, dark_mode=self._dark_mode).render()
            file_panel = FileExplorerPanel(self._runtime)
            with ui.column().classes("flex-1 min-w-0 h-full min-h-0 gap-4 px-6 py-4"):
                SessionTopBar(self._runtime, on_toggle_files=file_panel.toggle).render()
                MessageList(self._runtime).render()
                InputSection(self._runtime).render()
            file_panel.render()
