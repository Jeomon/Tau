from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from tau.modes.web.components.input_section import InputSection
from tau.modes.web.components.message_list import MessageList
from tau.modes.web.components.session_sidebar import SessionSidebar

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


class ChatPage:
    """Main browser chat page for one Tau runtime."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def render(self) -> None:
        """Render the chat page into the current NiceGUI page context."""
        with ui.row().classes("w-full h-[100vh] gap-0"):
            SessionSidebar(self._runtime).render()
            with ui.column().classes("flex-1 min-w-0 h-full min-h-0 gap-4 px-6 py-4"):
                MessageList(self._runtime).render()
                InputSection(self._runtime).render()
