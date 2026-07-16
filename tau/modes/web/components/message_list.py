from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role, TextContent
from tau.modes.web.components.message_view import MessageRole, MessageView, RenderedMessage

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_HOOK_NAMES = (
    "input",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "session_start",
)


def _message_text(message: object) -> str:
    """Return displayable text for a Tau message-like object."""
    if message is None:
        return ""
    text = getattr(message, "text_content", None)
    if callable(text):
        return str(text())
    contents = getattr(message, "contents", None)
    if contents is not None:
        return "".join(c.content for c in contents if isinstance(c, TextContent))
    return str(message or "")


def _message_role(message: object) -> MessageRole:
    """Map a session history message's role onto a chat bubble role."""
    return "user" if getattr(message, "role", None) == Role.USER else "assistant"


def _is_chat_message(message: object) -> bool:
    """True for plain user/assistant turns; false for tool calls, results, and other bookkeeping entries."""
    return getattr(message, "role", None) in {Role.USER, Role.ASSISTANT}


class MessageList:
    """Chat transcript for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._messages: list[RenderedMessage] = []
        self._assistant_message: RenderedMessage | None = None
        self._container: Any | None = None

    def render(self) -> None:
        """Render the message list and subscribe it to runtime message events."""
        with (
            ui.column().classes("w-full h-5/6 min-h-0 overflow-hidden"),
            ui.scroll_area().classes("w-full h-full"),
        ):
            self._container = ui.column().classes("w-full gap-4 pr-2")

        async def on_event(event: object) -> None:
            event_type = getattr(event, "type", "")
            if event_type == "input":
                self._append_message(str(getattr(event, "text", "")), role="user")
                return
            if event_type == "message_rollback":
                self._rollback_messages(int(getattr(event, "count", 0)))
                return
            if event_type == "session_start":
                self._replay_history()
                return

            message = getattr(event, "message", None)
            text = _message_text(message)
            if event_type == "message_start":
                self._assistant_message = self._append_message(text or "…", role="assistant")
                return
            if event_type in {"message_update", "message_end"}:
                self._update_assistant_message(text or "…")
                if event_type == "message_end":
                    self._assistant_message = None

        unsubs = [self._runtime.hooks.register(name, on_event) for name in _HOOK_NAMES]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

        self._replay_history()

    def _append_message(self, text: str, *, role: MessageRole) -> RenderedMessage | None:
        """Append a chat bubble and return its markdown element."""
        if self._container is None:
            return None

        with self._container:
            rendered = MessageView(text, role=role).render()

        self._messages.append(rendered)
        return rendered

    def _update_assistant_message(self, text: str) -> None:
        """Update the active assistant bubble, creating one if needed."""
        if self._assistant_message is None:
            self._assistant_message = self._append_message(text, role="assistant")
            return
        self._assistant_message.update_content(text)

    def _replay_history(self) -> None:
        """Clear the transcript and rebuild it from the (newly active) session."""
        if self._container is None:
            return

        self._container.clear()
        self._messages = []
        self._assistant_message = None

        context = self._runtime.session_manager.build_session_context()
        for message in context.messages:
            if not _is_chat_message(message):
                continue
            text = _message_text(message)
            if text:
                self._append_message(text, role=_message_role(message))

    def _rollback_messages(self, count: int) -> None:
        """Remove recently appended message bubbles."""
        for _ in range(max(count, 0)):
            if not self._messages:
                return
            self._messages.pop().delete()
        self._assistant_message = None
