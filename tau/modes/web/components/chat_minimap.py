from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role

if TYPE_CHECKING:
    from tau.modes.web.components.message_list import MessageList
    from tau.runtime.service import Runtime


class ChatMinimap:
    """Narrow scrollable strip alongside the transcript for quick jump navigation."""

    def __init__(self, runtime: Runtime, message_list: MessageList) -> None:
        self._runtime = runtime
        self._message_list = message_list
        self._container: Any | None = None

    def render(self) -> None:
        """Render the (initially empty) minimap and subscribe it to transcript updates."""
        self._container = ui.column().classes(
            "h-full min-h-0 items-center justify-center py-2 gap-[3px] tau-minimap"
        )
        self._refresh()

        async def on_event(event: object) -> None:
            if getattr(event, "type", "") in {"session_start", "message_end"}:
                self._refresh()

        unsubs = [
            self._runtime.hooks.register(name, on_event) for name in ("session_start", "message_end")
        ]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

    def _refresh(self) -> None:
        if self._container is None:
            return
        self._container.clear()

        context = self._runtime.session_manager.build_session_context()
        chat_messages = [
            m for m in context.messages if getattr(m, "role", None) in {Role.USER, Role.ASSISTANT}
        ]
        total = len(chat_messages)
        if total == 0:
            return

        with self._container:
            for index, message in enumerate(chat_messages):
                is_user = getattr(message, "role", None) == Role.USER
                classes = "w-5 h-[3px] rounded-full cursor-pointer flex-shrink-0 " + (
                    "tau-minimap-user" if is_user else "tau-minimap-assistant"
                )
                percent = index / max(total - 1, 1)
                marker = ui.row().classes(classes)
                marker.on("click", lambda p=percent: self._scroll_to(p))

    def _scroll_to(self, percent: float) -> None:
        scroll_area = self._message_list.scroll_area
        if scroll_area is not None:
            scroll_area.scroll_to(percent=percent, duration=0.3)
