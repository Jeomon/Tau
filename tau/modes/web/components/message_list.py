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
from tau.session.manager import SessionManager

if TYPE_CHECKING:
    from tau.message.types import AssistantMessage
    from tau.runtime.service import Runtime

_PROGRAMMATIC_SCROLL_IGNORE_S = 0.7
"""How long to ignore on_scroll events right after we trigger a scroll ourselves.

q-scroll-area fires `scroll` for both user-driven and programmatic scrolling
(including intermediate frames of an animated scroll_to), so without this
window a single auto-scroll call could be misread as the user scrolling away
and permanently disable auto-follow for the rest of the turn.
"""

_HOOK_NAMES = (
    "input",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "agent_end",
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
    """True for user/assistant turns; false for tools and bookkeeping entries."""
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


def _render_assistant_blocks(
    message: AssistantMessage,
    tool_results: dict[str, ToolResultContent],
    *,
    streaming: bool = False,
) -> None:
    """Render one assistant turn's text, thinking, and tool-call blocks in order.

    Must be called inside a `with <container>:` block — used for both history
    replay and live re-rendering of the in-progress turn.
    """
    for block in message.contents:
        if isinstance(block, TextContent):
            if block.content:
                MessageView(
                    block.content,
                    role="assistant",
                    timestamp=message.timestamp,
                    streaming=streaming,
                ).render()
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
        self._live_streaming = False
        self._live_tool_results: dict[str, ToolResultContent] = {}
        self.scroll_area: Any | None = None
        self._at_bottom = True
        self._ignore_scroll_until = 0.0

    def render(self) -> None:
        """Render the message list and subscribe it to runtime message events."""
        with ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"):
            scroll_area = ui.scroll_area(on_scroll=self._on_scroll).classes("w-full h-full")
            with scroll_area:
                self._container = ui.column().classes("w-full gap-4 pr-2")
            self.scroll_area = scroll_area

        async def on_event(event: object) -> None:
            event_type = getattr(event, "type", "")
            if event_type == "input":
                # Sending a message means "follow the reply" — re-arm
                # bottom-follow even if the user had scrolled away earlier.
                self._at_bottom = True
                self._append_message(
                    str(getattr(event, "text", "")), role="user", timestamp=time.time()
                )
                return
            if event_type == "message_rollback":
                self._rollback_messages(int(getattr(event, "count", 0)))
                return
            if event_type == "session_start":
                self._replay_history()
                return
            if event_type == "agent_end":
                self._live_streaming = False
                self._rerender_live_turn()
                return
            if event_type == "tool_execution_end":
                result = getattr(event, "tool_result", None)
                if result is not None:
                    self._live_tool_results[result.id] = result
                self._rerender_live_turn()
                return

            if event_type == "message_start":
                self._live_message = getattr(event, "message", None)
                self._live_streaming = True
                self._start_live_turn()
                return
            if event_type == "message_update":
                self._live_message = getattr(event, "message", None)
                self._rerender_live_turn()
                return
            if event_type == "message_end":
                self._live_message = getattr(event, "message", None)
                self._live_streaming = False
                self._rerender_live_turn()

        unsubs = [self._runtime.hooks.register(name, on_event) for name in _HOOK_NAMES]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])

        self._replay_history()

    def _on_scroll(self, event: Any) -> None:
        """Track whether new content should keep the transcript pinned to bottom."""
        if time.time() < self._ignore_scroll_until:
            # This event was caused by our own scroll_to call, not the user —
            # q-scroll-area fires `scroll` for animated programmatic scrolling
            # too, and without this window a single auto-scroll call could be
            # misread as the user scrolling away and disable auto-follow.
            return
        vertical_size = float(getattr(event, "vertical_size", 0) or 0)
        container_size = float(getattr(event, "vertical_container_size", 0) or 0)
        percentage = float(getattr(event, "vertical_percentage", 1) or 0)
        self._at_bottom = vertical_size <= container_size or percentage >= 0.98

    def _scroll_to_bottom(self, *, force: bool = False, animate: bool = False) -> None:
        if self.scroll_area is None:
            return
        if not force and not self._at_bottom:
            return
        self._ignore_scroll_until = time.time() + _PROGRAMMATIC_SCROLL_IGNORE_S
        self.scroll_area.scroll_to(percent=1.0, duration=0.2 if animate else 0.0)

    def set_compact(self, compact: bool) -> None:
        """Tighten or restore vertical spacing between messages."""
        if self._container is None:
            return
        if compact:
            self._container.classes(remove="gap-4", add="gap-1")
        else:
            self._container.classes(remove="gap-1", add="gap-4")

    def show_loading(self) -> None:
        """Show immediate feedback while another session is being loaded."""
        if self._container is None:
            return
        self._container.clear()
        self._messages = []
        self._live_container = None
        self._live_message = None
        self._live_streaming = False
        self._live_tool_results = {}
        self._at_bottom = True
        with (
            self._container,
            ui.column().classes("w-full h-[45vh] items-center justify-center gap-3"),
        ):
            ui.spinner(size="lg").style("color: var(--text-muted) !important;")
            ui.label("Loading session...").classes("text-xs text-[var(--text-muted)]")

    def _append_message(
        self,
        text: str,
        *,
        role: MessageRole,
        timestamp: float | None = None,
        auto_scroll: bool = True,
    ) -> RenderedMessage | None:
        """Append a chat bubble and return its markdown element."""
        if self._container is None:
            return None

        with self._container:
            rendered = MessageView(text, role=role, timestamp=timestamp).render()

        self._messages.append(rendered)
        if auto_scroll:
            self._scroll_to_bottom(force=True, animate=True)
        return rendered

    def _start_live_turn(self) -> None:
        """Open a fresh container for the in-progress assistant turn and render it."""
        if self._container is None:
            return
        should_scroll = self._at_bottom
        with self._container:
            root = ui.column().classes("w-full gap-2")
        self._live_container = root
        self._messages.append(RenderedMessage(root=root, content=root))
        self._live_streaming = True
        self._rerender_live_turn()
        if should_scroll:
            self._scroll_to_bottom(force=True, animate=True)

    def _rerender_live_turn(self) -> None:
        """Redraw the in-progress (or just-finished) assistant turn's blocks.

        Called on every message_update/message_end, and again on
        tool_execution_end so a tool call's result appears as soon as it's
        available even though the turn that issued it has already closed.
        Follows the transcript down to bottom as content streams in, as long
        as the user hasn't scrolled away (`self._at_bottom`).
        """
        if self._live_container is None or self._live_message is None:
            return
        should_scroll = self._at_bottom
        self._live_container.clear()
        with self._live_container:
            _render_assistant_blocks(
                self._live_message,  # type: ignore[arg-type]
                self._live_tool_results,
                streaming=self._live_streaming,
            )
        if should_scroll:
            self._scroll_to_bottom(force=True)

    def preview_session(self, session_file: Any) -> None:
        """Render a session file immediately without waiting for Runtime to switch."""
        try:
            manager = SessionManager(
                self._runtime.session_manager.cwd,
                session_dir=self._runtime.session_manager.session_dir,
                session_file=session_file,
                persist=False,
            )
        except Exception:
            self.show_loading()
            return
        self._replay_history(manager)

    def _replay_history(self, session_manager: SessionManager | None = None) -> None:
        """Clear the transcript and rebuild it from the (newly active) session."""
        if self._container is None:
            return

        self._container.clear()
        self._messages = []
        self._live_container = None
        self._live_message = None
        self._live_streaming = False
        self._live_tool_results = {}
        self._at_bottom = True

        manager = session_manager or self._runtime.session_manager
        context = manager.build_session_context()
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
                self._append_message(
                    text,
                    role="user",
                    timestamp=getattr(message, "timestamp", None),
                    auto_scroll=False,
                )
        self._scroll_to_bottom(force=True)

    def _rollback_messages(self, count: int) -> None:
        """Remove recently appended message bubbles."""
        for _ in range(max(count, 0)):
            if not self._messages:
                return
            self._messages.pop().delete()
        self._live_container = None
        self._live_message = None
