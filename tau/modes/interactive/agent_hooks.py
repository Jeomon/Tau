from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tau.modes.interactive.components.layout import Layout
    from tau.modes.interactive.components.message_list import MessageBlock
    from tau.runtime.service import Runtime
    from tau.tui.tui import TUI

# Flush streamed tokens to the block at most once per frame (~60fps).
# Markdown is re-parsed only on each flush, not on every token.
_STREAM_FLUSH_INTERVAL = 1 / 60


def _find_component(root: object, attr: str) -> object | None:
    """Depth-first search the component tree for one exposing ``attr``."""
    if root is None:
        return None
    if hasattr(root, attr):
        return root
    for child in getattr(root, "children", None) or []:
        found = _find_component(child, attr)
        if found is not None:
            return found
    for slot in getattr(root, "_slots", None) or []:
        comp = slot[0] if isinstance(slot, tuple) else slot
        found = _find_component(comp, attr)
        if found is not None:
            return found
    return None


class AgentHookHandler:
    """Subscribes to agent lifecycle hooks and drives the TUI in response.

    Owns all streaming state (current block, terminal block) so App does not
    need to track it. Call ``subscribe()`` after the agent is ready, then
    ``unsubscribe()`` (or use the returned unsub callables) on teardown.
    """

    def __init__(
        self,
        runtime: Runtime,
        layout: Layout,
        tui: TUI,
        on_palette_refresh: Callable[[], None] | None = None,
        on_turn_content: Callable[[], None] | None = None,
        on_settled: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._layout = layout
        self._tui = tui
        self._on_palette_refresh = on_palette_refresh
        self._on_turn_content = on_turn_content
        self._on_settled_cb = on_settled

        self._current_block: MessageBlock | None = None
        self._current_text_length: int = 0
        self._current_terminal_block: MessageBlock | None = None
        self._unsubs: list[Callable[[], None]] = []

        # Streaming batch state — pending token flush
        self._pending_msg: object = None
        self._pending_flush_handle: asyncio.TimerHandle | None = None
        self._last_flush_at: float = 0.0

    def subscribe(self) -> None:
        """Register all hook handlers on the current agent."""
        agent = self._runtime.agent
        if agent is None:
            return
        hooks = agent.hooks
        self._unsubs = [
            hooks.register("agent_start", self._on_agent_start),
            hooks.register("agent_end", self._on_agent_end),
            hooks.register("settled", self._on_settled),
            hooks.register("message_start", self._on_message_start),
            hooks.register("message_update", self._on_message_update),
            hooks.register("message_end", self._on_message_end),
            hooks.register("message_rollback", self._on_message_rollback),
            hooks.register("tool_execution_start", self._on_tool_start),
            hooks.register("tool_execution_end", self._on_tool_end),
            hooks.register("model_select", self._on_model_select),
            hooks.register("terminal_execution", self._on_terminal_execution),
            hooks.register("terminal_output", self._on_terminal_output),
            hooks.register("session_start", self._on_session_start),
            hooks.register("queue_update", self._on_queue_update),
            hooks.register("compaction_start", self._on_compaction_start),
            hooks.register("compaction_end", self._on_compaction_end),
        ]

    def unsubscribe(self) -> None:
        if self._pending_flush_handle is not None:
            self._pending_flush_handle.cancel()
            self._pending_flush_handle = None
        self._pending_msg = None
        self._current_block = None
        self._current_terminal_block = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # ── Session ───────────────────────────────────────────────────────────────

    async def _on_session_start(self, event: object) -> None:
        from tau.hooks.session import SessionStartReason

        reason = getattr(event, "reason", None)
        # Reasons that swap in a different message history: Fork/Resume/Clone
        # replay the new branch into the transcript; New just clears it.
        replay = reason in (
            SessionStartReason.Fork,
            SessionStartReason.Resume,
            SessionStartReason.Clone,
        )
        if reason == SessionStartReason.New or replay:
            self._layout.clear_messages()
            self._layout.spinner.stop()
            self._current_block = None
            self._current_text_length = 0
            self._current_terminal_block = None
            if replay:
                sm = self._runtime.session_manager
                if sm is not None:
                    ctx = sm.build_session_context()
                    for msg in ctx.messages:
                        # No finalize() here: a replayed AssistantMessage/ToolMessage
                        # can still hold thinking/tool-result content the user wants
                        # to reach via ctrl+o, which only affects still-live (not yet
                        # frozen) blocks — finalizing on arrival would make it
                        # permanently un-toggleable the moment it's added.
                        self._layout.add_message(msg)
        sm = self._runtime.session_manager
        if sm is not None:
            self._layout.set_cwd(sm.cwd)
        # Resume/branch swap in a different message history but run no agent turn,
        # so the footer's context-usage badge would otherwise keep the previous
        # session's value. Push fresh usage to it from this guaranteed refresh point.
        self._refresh_footer_context()
        if self._on_palette_refresh is not None:
            self._on_palette_refresh()
        self._tui.request_render()

    def _refresh_footer_context(self) -> None:
        """Re-push current context usage to the footer model badge, if present."""
        footer = getattr(self._layout, "footer", None)
        badge = _find_component(footer, "update_context_from_ctx")
        update = getattr(badge, "update_context_from_ctx", None)
        if not callable(update):
            return
        try:
            from tau.extensions.context import ExtensionContext

            update(ExtensionContext.from_runtime(self._runtime))
        except Exception:
            _log.debug("Extension context update failed", exc_info=True)

    # ── Agent lifecycle ───────────────────────────────────────────────────────

    async def _on_agent_start(self, _event: object) -> None:
        self._spinner(self._layout.spinner.theme.label_thinking, running=True)

    async def _on_agent_end(self, _event: object) -> None:
        self._spinner(running=False)

    async def _on_settled(self, _event: object) -> None:
        self._layout.set_pending_queue([], [])
        self._spinner(running=False)
        if self._on_settled_cb is not None:
            await self._on_settled_cb()

    async def _on_compaction_start(self, _event: object) -> None:
        # Layered reason so compaction can run alongside (and outlive) a turn's
        # spinner without either clobbering the other.
        self._layout.spinner.push_reason("compaction", self._layout.spinner.theme.label_compacting)
        self._tui.request_render()

    async def _on_compaction_end(self, _event: object) -> None:
        self._layout.spinner.pop_reason("compaction")
        self._tui.request_render()

    # ── Messages ──────────────────────────────────────────────────────────────

    async def _on_message_start(self, event: object) -> None:
        msg = getattr(event, "message", None)
        if msg is None:
            return
        self._layout.spinner.set_label(self._layout.spinner.theme.label_thinking)
        block = self._layout.add_message(msg, streaming=False)
        self._current_block = block
        self._current_text_length = _text_length(msg)
        self._tui.request_render()

    async def _on_message_update(self, event: object) -> None:
        from tau.message.types import TextContent, ThinkingContent

        msg = getattr(event, "message", None)
        if msg is None or self._current_block is None:
            return

        # Update spinner label immediately — zero cost.
        contents = getattr(msg, "contents", [])
        if contents:
            self._mark_turn_content()
            last = contents[-1]
            if isinstance(last, ThinkingContent):
                self._layout.spinner.set_label(self._layout.spinner.theme.label_thinking)
            elif isinstance(last, TextContent) and last.content:
                self._layout.spinner.set_label(self._layout.spinner.theme.label_streaming)

        # Buffer the latest message; schedule a flush if none pending.
        self._pending_msg = msg
        if self._pending_flush_handle is None:
            elapsed = time.monotonic() - self._last_flush_at
            delay = max(0.0, _STREAM_FLUSH_INTERVAL - elapsed)
            loop = asyncio.get_event_loop()
            self._pending_flush_handle = loop.call_later(delay, self._flush_pending)

    def _flush_pending(self) -> None:
        """Flush the buffered token batch: re-parse markdown once, then render."""
        self._pending_flush_handle = None
        msg = self._pending_msg
        self._pending_msg = None
        if msg is None or self._current_block is None:
            return
        tl = _text_length(msg)
        self._update_block(msg, streaming=tl > self._current_text_length)
        self._current_text_length = tl
        self._last_flush_at = time.monotonic()
        self._tui.request_render()

    async def _on_message_end(self, event: object) -> None:
        from tau.message.types import AssistantMessage, ToolMessage

        # Cancel any pending batch flush — message_end supersedes it.
        if self._pending_flush_handle is not None:
            self._pending_flush_handle.cancel()
            self._pending_flush_handle = None
        self._pending_msg = None
        self._last_flush_at = 0.0

        msg = getattr(event, "message", None)
        if msg is None:
            return
        if isinstance(msg, (AssistantMessage, ToolMessage)):
            self._mark_turn_content()
            if self._current_block is not None:
                self._update_block(msg, streaming=False, clear=True)
            else:
                # No finalize(): a ToolMessage's result is a ctrl+o target too
                # (toggle_details_expanded), which only reaches still-live
                # blocks — freezing on arrival would make it permanently
                # un-toggleable immediately.
                self._layout.add_message(msg)
        self._tui.request_render()

    async def _on_message_rollback(self, event: object) -> None:
        """Remove the last ``count`` message blocks from the transcript.

        Mirrors the engine dropping an interrupted tool turn: cancel any pending
        flush, drop the assistant tool-call block and its tool-result block, and
        reset the streaming cursor so the following interrupt marker renders fresh.
        """
        count = getattr(event, "count", 0)
        if self._pending_flush_handle is not None:
            self._pending_flush_handle.cancel()
            self._pending_flush_handle = None
        self._pending_msg = None
        for _ in range(count):
            if not self._layout.messages.remove_last():
                break
        self._current_block = None
        self._current_text_length = 0
        self._tui.request_render()

    # ── Tools ─────────────────────────────────────────────────────────────────

    async def _on_tool_start(self, _event: object) -> None:
        self._mark_turn_content()
        self._spinner(self._layout.spinner.theme.label_tool_calling)

    async def _on_tool_end(self, _event: object) -> None:
        self._spinner(self._layout.spinner.theme.label_thinking)

    # ── Terminal ──────────────────────────────────────────────────────────

    async def _on_terminal_execution(self, event: object) -> None:
        msg = getattr(event, "message", None)
        streaming = getattr(event, "streaming", False)
        if msg is None:
            return
        if streaming:
            block = self._layout.add_message(msg, streaming=True)
            self._current_terminal_block = block
        else:
            if self._current_terminal_block is not None:
                self._current_terminal_block.set_streaming(False)
                # Dropping our own reference for good right here — safe to
                # freeze immediately even if it's still the last message.
                self._current_terminal_block.finalize()
                self._current_terminal_block = None
        self._tui.request_render()

    async def _on_terminal_output(self, _event: object) -> None:
        if self._current_terminal_block is not None:
            self._current_terminal_block.invalidate()
            self._tui.request_render()

    # ── Model / queue ─────────────────────────────────────────────────────────

    async def _on_model_select(self, _event: object) -> None:
        if self._on_palette_refresh is not None:
            self._on_palette_refresh()
        self._tui.request_render()

    async def _on_queue_update(self, event: object) -> None:
        from tau.message.types import TextContent

        engine_state = None
        agent = self._runtime.agent
        if agent is not None:
            engine_state = agent._engine.state

        steering: list[str] = []
        followup: list[str] = []

        if engine_state is not None:
            for queue, out in [
                (engine_state.steering_queue, steering),
                (engine_state.follow_up_queue, followup),
            ]:
                if queue:
                    for msg in queue.snapshot():
                        text = "".join(
                            c.content
                            for c in getattr(msg, "contents", [])
                            if isinstance(c, TextContent)
                        )
                        if text:
                            out.append(text)

        # Only show pending queue display if there are actual messages waiting.
        # This prevents showing stale queue hints after messages are drained.
        if steering or followup:
            self._layout.set_pending_queue(steering, followup)
        else:
            self._layout.set_pending_queue([], [])
        self._tui.request_render()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mark_turn_content(self) -> None:
        """Tell the input handler the assistant has produced output this turn."""
        if self._on_turn_content is not None:
            self._on_turn_content()

    def _spinner(self, label: str | None = None, *, running: bool | None = None) -> None:
        if label is not None:
            self._layout.spinner.set_label(label)
        if running is True:
            self._layout.spinner.start()
        elif running is False:
            self._layout.spinner.stop()
        self._tui.request_render()

    def _update_block(self, msg: object, *, streaming: bool, clear: bool = False) -> None:
        if self._current_block is None:
            return
        self._current_block._message = msg
        self._current_block.set_streaming(streaming)
        self._current_block.invalidate()
        if clear:
            # No finalize(): this is always an AssistantMessage/ToolMessage,
            # a ctrl+o target — toggle_details_expanded only reaches
            # still-live blocks, so freezing here the instant a reply
            # finishes would make it permanently un-toggleable. It still
            # freezes safely once something else is added after it (the
            # "not last unit" fallback in render_split_cells).
            self._current_block = None
            self._current_text_length = 0

    def _refresh_model_badge(self) -> None:
        if self._on_palette_refresh is not None:
            self._on_palette_refresh()


def _text_length(message: object) -> int:
    from tau.message.types import TextContent

    contents = getattr(message, "contents", [])
    return sum(len(item.content) for item in contents if isinstance(item, TextContent))
