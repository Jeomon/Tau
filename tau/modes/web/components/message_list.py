from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role, TextContent, ThinkingContent, ToolCallContent, ToolResultContent
from tau.modes.web.components.message_view import (
    MessageRole,
    MessageView,
    RenderedMessage,
    render_thinking_block,
    render_tool_call_block,
)

if TYPE_CHECKING:
    from tau.message.types import AssistantMessage
    from tau.runtime.service import Runtime

_HOOK_NAMES = (
    "input",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "session_start",
    "tool_execution_end",
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


def _is_chat_message(message: object) -> bool:
    """True for plain user/assistant turns; false for tool calls, results, and other bookkeeping entries."""
    return getattr(message, "role", None) in {Role.USER, Role.ASSISTANT}


def _collect_tool_results(messages: Sequence[object]) -> dict[str, ToolResultContent]:
    """Map tool_call id -> its result, gathered from every ToolMessage in the session."""
    results: dict[str, ToolResultContent] = {}
    for message in messages:
        if getattr(message, "role", None) != Role.TOOL:
            continue
        for block in getattr(message, "contents", []):
            if isinstance(block, ToolResultContent):
                results[block.id] = block
    return results


def _render_assistant_blocks(message: AssistantMessage, tool_results: dict[str, ToolResultContent]) -> None:
    """Render one assistant turn's text, thinking, and tool-call blocks in order.

    Must be called inside a `with <container>:` block — used for both history
    replay and live re-rendering of the in-progress turn.
    """
    for block in message.contents:
        if isinstance(block, TextContent):
            if block.content:
                MessageView(block.content, role="assistant", timestamp=message.timestamp).render()
        elif isinstance(block, ThinkingContent):
            render_thinking_block(block)
        elif isinstance(block, ToolCallContent):
            render_tool_call_block(block, tool_results.get(block.id))


class MessageList:
    """Chat transcript for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._messages: list[RenderedMessage] = []
        self._container: Any | None = None
        self._live_container: Any | None = None
        self._live_message: object | None = None
        self._live_tool_results: dict[str, ToolResultContent] = {}
        self.scroll_area: Any | None = None

    def render(self) -> None:
        """Render the message list and subscribe it to runtime message events."""
        with ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"):
            scroll_area = ui.scroll_area().classes("w-full h-full")
            with scroll_area:
                self._container = ui.column().classes("w-full gap-4 pr-2")
            self.scroll_area = scroll_area

        async def on_event(event: object) -> None:
            event_type = getattr(event, "type", "")
            if event_type == "input":
                self._append_message(str(getattr(event, "text", "")), role="user", timestamp=time.time())
                return
            if event_type == "message_rollback":
                self._rollback_messages(int(getattr(event, "count", 0)))
                return
            if event_type == "session_start":
                self._replay_history()
                return
            if event_type == "tool_execution_end":
                result = getattr(event, "tool_result", None)
                if result is not None:
                    self._live_tool_results[result.id] = result
                self._rerender_live_turn()
                return

            if event_type == "message_start":
                self._live_message = getattr(event, "message", None)
                self._start_live_turn()
                return
            if event_type in {"message_update", "message_end"}:
                self._live_message = getattr(event, "message", None)
                self._rerender_live_turn()

        unsubs = [self._runtime.hooks.register(name, on_event) for name in _HOOK_NAMES]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

        self._replay_history()

    def set_compact(self, compact: bool) -> None:
        """Tighten or restore vertical spacing between messages."""
        if self._container is None:
            return
        if compact:
            self._container.classes(remove="gap-4", add="gap-1")
        else:
            self._container.classes(remove="gap-1", add="gap-4")

    def _append_message(
        self, text: str, *, role: MessageRole, timestamp: float | None = None
    ) -> RenderedMessage | None:
        """Append a chat bubble and return its markdown element."""
        if self._container is None:
            return None

        with self._container:
            rendered = MessageView(text, role=role, timestamp=timestamp).render()

        self._messages.append(rendered)
        return rendered

    def _start_live_turn(self) -> None:
        """Open a fresh container for the in-progress assistant turn and render it."""
        if self._container is None:
            return
        with self._container:
            root = ui.column().classes("w-full gap-2")
        self._live_container = root
        self._messages.append(RenderedMessage(root=root, content=root))
        self._rerender_live_turn()

    def _rerender_live_turn(self) -> None:
        """Redraw the in-progress (or just-finished) assistant turn's blocks.

        Called on every message_update/message_end, and again on
        tool_execution_end so a tool call's result appears as soon as it's
        available even though the turn that issued it has already closed.
        """
        if self._live_container is None or self._live_message is None:
            return
        self._live_container.clear()
        with self._live_container:
            _render_assistant_blocks(self._live_message, self._live_tool_results)  # type: ignore[arg-type]

    def _replay_history(self) -> None:
        """Clear the transcript and rebuild it from the (newly active) session."""
        if self._container is None:
            return

        self._container.clear()
        self._messages = []
        self._live_container = None
        self._live_message = None
        self._live_tool_results = {}

        context = self._runtime.session_manager.build_session_context()
        tool_results = _collect_tool_results(context.messages)
        for message in context.messages:
            if not _is_chat_message(message):
                continue
            if getattr(message, "role", None) == Role.ASSISTANT:
                with self._container:
                    _render_assistant_blocks(message, tool_results)  # type: ignore[arg-type]
                continue
            text = _message_text(message)
            if text:
                self._append_message(text, role="user", timestamp=getattr(message, "timestamp", None))

    def _rollback_messages(self, count: int) -> None:
        """Remove recently appended message bubbles."""
        for _ in range(max(count, 0)):
            if not self._messages:
                return
            self._messages.pop().delete()
        self._live_container = None
        self._live_message = None
