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

_HOOK_NAMES = (
    "input",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "agent_end",
    "agent_error",
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
        self._waiting_row: Any | None = None
        self._waiting_label: Any | None = None
        self._waiting_timer: Any | None = None
        self._waiting_started_at = 0.0

    def render(self) -> None:
        """Render the message list and subscribe it to runtime message events."""
        with ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"):
            scroll_area = ui.scroll_area().classes("w-full h-full")
            with scroll_area:
                self._container = ui.column().classes("w-full gap-4 pr-2")
            self.scroll_area = scroll_area
            self._install_client_auto_scroll()

        async def on_event(event: object) -> None:
            event_type = getattr(event, "type", "")
            if event_type == "input":
                # Sending a message means "follow the reply" — snap to bottom
                # and re-arm auto-follow even if scrolled away earlier.
                self._append_message(
                    str(getattr(event, "text", "")), role="user", timestamp=time.time()
                )
                self._client_scroll_to_bottom(smooth=True)
                self._start_waiting_indicator()
                return
            if event_type == "message_rollback":
                self._rollback_messages(int(getattr(event, "count", 0)))
                return
            if event_type == "session_start":
                self._replay_history()
                return
            if event_type == "agent_end":
                self._clear_waiting_indicator()
                self._live_streaming = False
                self._rerender_live_turn()
                return
            if event_type == "agent_error":
                # A turn can fail before any content streams (e.g. every
                # transient-error retry inside TextLLM exhausted) — in that
                # case message_start never fires, so without this the
                # transcript just silently stops with no explanation and
                # looks hung. Mirrors the TUI surfacing this via its spinner.
                self._clear_waiting_indicator()
                self._live_streaming = False
                error_text = str(getattr(event, "error", "") or "Unknown error")
                self._append_error(error_text)
                return
            if event_type == "tool_execution_end":
                result = getattr(event, "tool_result", None)
                if result is not None:
                    self._live_tool_results[result.id] = result
                self._rerender_live_turn()
                return

            if event_type == "message_start":
                self._clear_waiting_indicator()
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

        def on_disconnect() -> None:
            self._clear_waiting_indicator()
            for unsub in unsubs:
                unsub()

        ui.context.client.on_disconnect(on_disconnect)

        self._replay_history()

    def _install_client_auto_scroll(self) -> None:
        """Wire up client-side stick-to-bottom behavior for the transcript.

        This intentionally lives entirely in the browser instead of round-
        tripping through the server on every streamed chunk. A streaming
        reply can mutate the DOM many times a second; if the "should we
        follow the user down" decision depended on a server round-trip
        (Python scroll-position tracking + a Python-issued scroll_to), a
        chunk arriving while the user's manual scroll event is still in
        flight would win the race and yank them back to the bottom even
        though they were actively scrolling away. Doing both the tracking
        (native `scroll` listener) and the reaction (`MutationObserver` on
        the content div) synchronously in JS removes that race entirely.
        """
        if self.scroll_area is None:
            return
        self.scroll_area.client.run_javascript(
            f"""
            (function() {{
                var root = document.getElementById('c{self.scroll_area.id}');
                if (!root) return;
                var container = root.querySelector('.q-scrollarea__container');
                var content = root.querySelector('.q-scrollarea__content');
                if (!container || !content || container.__tauAutoScroll) return;
                container.__tauAutoScroll = true;

                var THRESHOLD = 48;
                var state = {{ atBottom: true }};
                container.__tauScrollState = state;

                function isNearBottom() {{
                    return container.scrollHeight - container.scrollTop - container.clientHeight < THRESHOLD;
                }}
                container.addEventListener('scroll', function() {{
                    state.atBottom = isNearBottom();
                }}, {{ passive: true }});

                var observer = new MutationObserver(function() {{
                    if (state.atBottom) {{
                        container.scrollTop = container.scrollHeight;
                    }}
                }});
                observer.observe(content, {{ childList: true, subtree: true, characterData: true }});
            }})();
            """
        )

    def _client_scroll_to_bottom(self, *, smooth: bool = False) -> None:
        """Force-scroll to bottom and re-arm client-side auto-follow."""
        if self.scroll_area is None:
            return
        behavior = "smooth" if smooth else "auto"
        self.scroll_area.client.run_javascript(
            f"""
            (function() {{
                var root = document.getElementById('c{self.scroll_area.id}');
                if (!root) return;
                var container = root.querySelector('.q-scrollarea__container');
                if (!container) return;
                if (container.__tauScrollState) container.__tauScrollState.atBottom = true;
                container.scrollTo({{ top: container.scrollHeight, behavior: '{behavior}' }});
            }})();
            """
        )

    def show_loading(self) -> None:
        """Show immediate feedback while another session is being loaded."""
        if self._container is None:
            return
        self._clear_waiting_indicator()
        self._container.clear()
        self._messages = []
        self._live_container = None
        self._live_message = None
        self._live_streaming = False
        self._live_tool_results = {}
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
    ) -> RenderedMessage | None:
        """Append a chat bubble and return its markdown element.

        No explicit scroll call needed — the client-side MutationObserver
        installed by `_install_client_auto_scroll` follows this automatically
        if the user is at the bottom.
        """
        if self._container is None:
            return None

        with self._container:
            rendered = MessageView(text, role=role, timestamp=timestamp).render()

        self._messages.append(rendered)
        return rendered

    def _start_waiting_indicator(self) -> None:
        """Show a "waiting for the model" row while nothing has streamed yet.

        A turn can go quiet for a while before any content arrives — most
        commonly TextLLM's own transient-error retry loop (exponential
        backoff across a few attempts), which currently only logs server-side
        with no hook event a UI can subscribe to. Rather than plumb attempt
        counts through the shared inference layer, this just surfaces elapsed
        wait time so the transcript never looks silently hung. Cleared by
        `_clear_waiting_indicator` as soon as message_start/agent_end/agent_error
        fires.
        """
        if self._container is None:
            return
        self._clear_waiting_indicator()
        self._waiting_started_at = time.time()
        with self._container:
            row = ui.row().classes("w-full items-center gap-2 px-1")
            with row:
                ui.spinner(size="sm").classes("text-[var(--text-dim)]")
                label = ui.label("Waiting for response…").classes(
                    "text-xs text-[var(--text-dim)]"
                )
        self._waiting_row = row
        self._waiting_label = label

        def tick() -> None:
            if self._waiting_label is None:
                return
            elapsed = int(time.time() - self._waiting_started_at)
            suffix = " — the provider may be retrying a transient error" if elapsed >= 6 else ""
            self._waiting_label.text = f"Waiting for response… ({elapsed}s){suffix}"

        self._waiting_timer = ui.timer(1.0, tick)
        self._client_scroll_to_bottom(smooth=True)

    def _clear_waiting_indicator(self) -> None:
        if self._waiting_timer is not None:
            self._waiting_timer.cancel()
            self._waiting_timer = None
        if self._waiting_row is not None:
            self._waiting_row.delete()
            self._waiting_row = None
        self._waiting_label = None

    def _append_error(self, text: str) -> None:
        """Append a visible error notice, distinct from a normal chat bubble."""
        if self._container is None:
            return
        with self._container:
            root = ui.row().classes("w-full items-start gap-2 px-3 py-2 tau-tool-error rounded-lg")
            with root:
                ui.icon("error_outline").classes("text-[#f87171]").style("font-size: 18px;")
                ui.markdown(text).classes("text-xs text-[#f87171] whitespace-pre-wrap flex-1")
        self._messages.append(RenderedMessage(root=root, content=root))
        self._client_scroll_to_bottom(smooth=True)

    def _start_live_turn(self) -> None:
        """Open a fresh container for the in-progress assistant turn and render it."""
        if self._container is None:
            return
        with self._container:
            root = ui.column().classes("w-full gap-2")
        self._live_container = root
        self._messages.append(RenderedMessage(root=root, content=root))
        self._live_streaming = True
        self._rerender_live_turn()

    def _rerender_live_turn(self) -> None:
        """Redraw the in-progress (or just-finished) assistant turn's blocks.

        Called on every message_update/message_end, and again on
        tool_execution_end so a tool call's result appears as soon as it's
        available even though the turn that issued it has already closed.
        No explicit scroll call needed here either — see `_append_message`.
        """
        if self._live_container is None or self._live_message is None:
            return
        self._live_container.clear()
        with self._live_container:
            _render_assistant_blocks(
                self._live_message,  # type: ignore[arg-type]
                self._live_tool_results,
                streaming=self._live_streaming,
            )

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

        self._clear_waiting_indicator()
        self._container.clear()
        self._messages = []
        self._live_container = None
        self._live_message = None
        self._live_streaming = False
        self._live_tool_results = {}

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
                )
        self._client_scroll_to_bottom(smooth=False)

    def _rollback_messages(self, count: int) -> None:
        """Remove recently appended message bubbles."""
        for _ in range(max(count, 0)):
            if not self._messages:
                return
            self._messages.pop().delete()
        self._live_container = None
        self._live_message = None
