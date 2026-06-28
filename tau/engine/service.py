from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tau.engine.types import (
    AbortSignal,
    AgentEndEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentStartEvent,
    EmitEvent,
    EngineOptions,
    EngineState,
    FollowupQueue,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    SteeringQueue,
    ToolExecutionEndEvent,
    ToolExecutionFailureEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau.hooks.engine import (
    AgentEndReason,
    MessageRollbackEvent,
    ToolResultEvent,
    ToolResultEventResult,
)
from tau.hooks.inference import AfterProviderResponseEvent, BeforeProviderRequestEvent
from tau.hooks.service import Hooks
from tau.hooks.tui import QueueUpdateEvent
from tau.inference.types import (
    EndEvent,
    ErrorEvent,
    LLMContext,
    StopReason,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallEndEvent,
)
from tau.message.types import (
    AssistantMessage,
    LLMMessage,
    Role,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    Usage,
)
from tau.tool.types import ToolContext, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from tau.agent.types import AgentContext
    from tau.inference.api.text.service import TextLLM as LLM
    from tau.settings.manager import SettingsManager
    from tau.tool.types import Tool

_log = logging.getLogger(__name__)


class Engine:
    """
    Raw LLM streaming loop and tool execution layer.

    Knows nothing about sessions, extensions, or compaction — those concerns
    belong to Agent.  Callers drive it via run() / run_continue() and observe
    results through the event callbacks wired in Options.

    Workflow:

    1. **Initialization**: Sets up LLM, tools, and state (e.g., EngineState).
    2. **Loop Execution**: Streams LLM responses, executes tools, and handles events.
    3. **Continuation**: Resumes from saved state via run_continue().
    4. **Error Handling**: Catches exceptions and emits AgentErrorEvent.

    Example Usage::

        from tau.engine import Agent, AgentState, Options

        # Initialize
        agent = Agent(cwd=Path("/path"), llm=llm, tools=tools)

        # Run
        await agent.run(ctx=AgentContext(messages=[...]))

        # Handle events
        await agent.subscribe(lambda event: print(event))
    """

    def __init__(
        self,
        cwd: Path,
        llm: LLM,
        tools: list[Tool],
        system_prompt: str | None = None,
        options: EngineOptions | None = None,
        hooks: Hooks | None = None,
        settings: SettingsManager | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.options = options or EngineOptions()
        self.hooks = hooks or Hooks()
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self._settings = settings
        self.tool_context = ToolContext(llm=llm, cwd=cwd, settings=settings)
        self.state = EngineState(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            follow_up_queue=FollowupQueue(mode=self.options.followup_mode),
            steering_queue=SteeringQueue(mode=self.options.steering_mode),
        )
        self._signal: asyncio.Event = asyncio.Event()
        self._subscribers: list = []
        # Set by tools that need to trigger a deferred action after the current
        # turn is fully saved (e.g. reboot).  Checked by Agent.invoke() after
        # _run_with_retry() returns — never called mid-turn.
        self._deferred_fn: Callable[[], Coroutine] | None = None

    async def subscribe(self, handler) -> Callable[[], None]:
        """Register an event handler (sync or async).

        Args:
            handler: A callable that receives AgentEvent objects.

        Returns:
            An unsubscribe callable that removes the handler when invoked.
        """
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    async def steer(self, message: LLMMessage) -> None:
        """Enqueue a steering message to be injected after the next tool-call round-trip.

        Args:
            message: An LLM message to inject into the context.
        """
        if self.state.steering_queue:
            await self.state.steering_queue.enqueue(message)
            await self.hooks.emit(
                QueueUpdateEvent(
                    queue="steering",
                    message=message,
                    messages=self.state.steering_queue.snapshot(),
                )
            )

    async def follow_up(self, message: LLMMessage) -> None:
        """Enqueue a follow-up message to be injected after the current stop-reason=Stop turn.

        Args:
            message: An LLM message to inject after the agent finishes naturally.
        """
        if self.state.follow_up_queue:
            await self.state.follow_up_queue.enqueue(message)
            await self.hooks.emit(
                QueueUpdateEvent(
                    queue="followup",
                    message=message,
                    messages=self.state.follow_up_queue.snapshot(),
                )
            )

    def clear_steering(self) -> None:
        """Discard all pending steering messages without consuming them."""
        if self.state.steering_queue:
            self.state.steering_queue.clear()

    def clear_follow_up(self) -> None:
        """Discard all pending follow-up messages without consuming them."""
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        """Discard all queued steering and follow-up messages."""
        if self.state.steering_queue:
            self.state.steering_queue.clear()
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()

    def has_pending_messages(self) -> bool:
        """True if the steering or follow-up queue has messages waiting to be consumed."""
        steering_has = (
            self.state.steering_queue is not None and not self.state.steering_queue.is_empty()
        )
        followup_has = (
            self.state.follow_up_queue is not None and not self.state.follow_up_queue.is_empty()
        )
        return steering_has or followup_has

    def reset(self) -> None:
        """Clear transient turn state so the engine can be re-run after an error."""
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()
        if self.state.steering_queue:
            self.state.steering_queue.clear()
        self.state.error_message = None
        self.state.pending_tool_calls.clear()
        self.state.is_streaming = False
        self.state.idle_event.set()

    def set_llm(self, llm: LLM) -> None:
        """Swap the active LLM. Only safe to call when the engine is idle.

        Args:
            llm: The new TextLLM instance to use for subsequent turns.

        Raises:
            RuntimeError: If the engine is currently streaming.
        """
        if self.state.is_streaming:
            raise RuntimeError("Cannot change model while agent is running.")
        self.llm = llm
        self.tool_context = ToolContext(llm=llm, cwd=self.tool_context.cwd, settings=self._settings)
        self.state.llm = llm

    def abort(self) -> None:
        """Signal the running loop to stop at the next safe check point."""
        self._signal.set()

    @property
    def is_idle(self) -> bool:
        """True when no streaming loop is active; safe to call run() or run_continue()."""
        return not self.state.is_streaming

    async def wait_for_idle(self) -> None:
        """Wait until the streaming loop exits."""
        await self.state.idle_event.wait()

    async def process_events(self, event: AgentEvent) -> None:
        """Update engine state from an event and broadcast it to hooks and subscribers.

        Args:
            event: An AgentEvent to process and emit.
        """
        match event:
            case MessageStartEvent(message=message):
                self.state.streaming_message = message
            case MessageUpdateEvent(message=message):
                self.state.streaming_message = message
            case MessageEndEvent(message=message):
                self.state.streaming_message = None
                if message:
                    self.state.messages.append(message)
            case MessageRollbackEvent(count=count):
                if count > 0:
                    del self.state.messages[-count:]
            case ToolExecutionStartEvent(tool_call=tool_call):
                self.state.pending_tool_calls.add(tool_call.id)
            case ToolExecutionEndEvent(tool_result=tool_result):
                self.state.pending_tool_calls.discard(tool_result.id)
            case AgentErrorEvent(error=error):
                self.state.error_message = error

        await self.hooks.emit(event)
        if self.options.on_event is not None:
            await self.options.on_event(event)
        for handler in list(self._subscribers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result

    # -------------------------------------------------------------------------
    # Tool execution
    # -------------------------------------------------------------------------

    async def _execute(
        self,
        tool_call: ToolCallContent,
        emit: EmitEvent,
        signal: AbortSignal | None,
    ) -> ToolResultContent:
        """Validate, run before/after hooks, and execute a single tool call.

        Args:
            tool_call: The tool call to execute.
            emit: Callback to emit execution events.
            signal: Abort signal to check for cancellation.

        Returns:
            A ToolResultContent with the tool's result or an error message.
        """
        if self.options.should_skip_tool_calls is not None:
            return self.options.should_skip_tool_calls(tool_call)

        tool = self._tools.get(tool_call.name)
        if tool is None:
            _log.warning("tool not found: %s", tool_call.name)
            return ToolResultContent(
                id=tool_call.id,
                is_error=True,
                content=f"Tool '{tool_call.name}' not found.",
                metadata={},
            )

        tool_call.kind = tool.kind
        ok, errors = tool.validate(params=tool_call.args)
        if not ok:
            _log.debug("tool %s: invalid params: %s", tool_call.name, "; ".join(errors))
            content = f"Invalid parameters for '{tool_call.name}':\n{chr(10).join(errors)}"
            return ToolResultContent(id=tool_call.id, is_error=True, content=content, metadata={})

        args = tool_call.args
        if tool.prepare_arguments is not None:
            args = tool.prepare_arguments(args) or args

        invocation = ToolInvocation(
            id=tool_call.id, params=args, name=tool_call.name, cwd=self.tool_context.cwd
        )

        # before hook — returning ToolResultContent cancels execution
        if self.options.before_tool_call is not None:
            before_result = await self.options.before_tool_call(invocation, signal)
            if isinstance(before_result, ToolResultContent):
                await emit(ToolExecutionEndEvent(tool_result=before_result))
                return before_result
            elif before_result is not None:
                invocation = before_result

        async def on_update(partial: ToolResult) -> None:
            await emit(ToolExecutionUpdateEvent(partial_tool_result=partial))

        _log.debug("tool call: %s", tool_call.name)
        tool_result: ToolResultContent
        try:
            await emit(ToolExecutionStartEvent(tool_call=tool_call))
            raw = await tool.execute(
                invocation=invocation,
                tool_execution_update_callback=on_update,
                signal=signal,
                context=self.tool_context,
            )
            if self.options.after_tool_call is not None:
                raw = await self.options.after_tool_call(invocation, raw, signal) or raw
            tool_result = ToolResultContent(
                id=tool_call.id,
                is_error=raw.is_error,
                content=raw.content,
                metadata=raw.metadata,
                terminate=raw.terminate,
                terminate_message=raw.terminate_message,
                tool_name=tool_call.name,
            )
        except Exception as e:
            _log.error("tool %s raised: %s", tool_call.name, e, exc_info=True)
            error = f"Tool '{tool_call.name}' execution failed:\n{e}"
            tool_result = ToolResultContent(
                id=tool_call.id,
                is_error=True,
                content=error,
                metadata={},
                tool_name=tool_call.name,
            )
            await emit(
                ToolExecutionFailureEvent(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    input=tool_call.args,
                    error=error,
                )
            )

        await emit(ToolExecutionEndEvent(tool_result=tool_result))

        hook_results = await self.hooks.emit(
            ToolResultEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                input=tool_call.args,
                content=tool_result.content,
                is_error=tool_result.is_error,
            )
        )
        for r in hook_results:
            if isinstance(r, ToolResultEventResult):
                if r.content is not None:
                    tool_result.content = r.content
                if r.is_error is not None:
                    tool_result.is_error = r.is_error
                if r.terminate:
                    tool_result.terminate = True
                if r.metadata is not None:
                    tool_result.metadata = {**(tool_result.metadata or {}), **r.metadata}
                break

        return tool_result

    async def _sequential_execute(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: AbortSignal | None,
    ) -> list[ToolResultContent]:
        """Execute tool calls one at a time, preserving invocation order.

        Args:
            tool_calls: List of tool calls to execute sequentially.
            emit: Callback to emit execution events.
            signal: Abort signal to check for cancellation.

        Returns:
            List of ToolResultContent in the same order as tool_calls.
        """
        results = []
        for tc in tool_calls:
            # Stop launching further tools once the user aborts; the interrupted
            # turn is dropped wholesale, so partial results aren't needed.
            if signal is not None and signal.is_set():
                break
            results.append(await self._execute(tc, emit, signal))
        return results

    async def _parallel_execute(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: AbortSignal | None,
    ) -> list[ToolResultContent]:
        """Execute all tool calls concurrently via asyncio.gather.

        Args:
            tool_calls: List of tool calls to execute in parallel.
            emit: Callback to emit execution events.
            signal: Abort signal to check for cancellation.

        Returns:
            List of ToolResultContent (order may differ from input).
        """
        return list(await asyncio.gather(*[self._execute(tc, emit, signal) for tc in tool_calls]))

    async def _batch_execute(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: AbortSignal | None,
    ) -> list[ToolResultContent]:
        """Split tool calls by each tool's own execution_mode, run parallel group
        concurrently and sequential group one-at-a-time, then merge results."""
        results: list[ToolResultContent] = []
        parallel_calls: list[ToolCallContent] = []
        sequential_calls: list[ToolCallContent] = []
        for tc in tool_calls:
            tool = self._tools.get(tc.name)
            if tool is None:
                results.append(
                    ToolResultContent(
                        id=tc.id,
                        is_error=True,
                        content=f"Tool '{tc.name}' not found.",
                        metadata={},
                    )
                )
                continue
            match tool.execution_mode:
                case ToolExecutionMode.Parallel:
                    parallel_calls.append(tc)
                case ToolExecutionMode.Sequential | _:
                    sequential_calls.append(tc)
        if parallel_calls:
            results.extend(await self._parallel_execute(parallel_calls, emit, signal))
        if sequential_calls:
            results.extend(await self._sequential_execute(sequential_calls, emit, signal))
        return results

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: AbortSignal | None = None,
    ) -> list[ToolResultContent]:
        """Dispatch tool calls according to the configured execution mode."""
        match self.options.execution_mode:
            case ToolExecutionMode.Parallel:
                return await self._parallel_execute(tool_calls, emit, signal)
            case ToolExecutionMode.Batch:
                return await self._batch_execute(tool_calls, emit, signal)
            case ToolExecutionMode.Sequential | _:
                return await self._sequential_execute(tool_calls, emit, signal)

    # -------------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------------

    @staticmethod
    async def _iter_with_abort(stream: Any, signal: AbortSignal):
        """Yield events from ``stream`` while honouring ``signal`` immediately.

        ``async for event in stream`` only re-checks the abort signal once the
        next event arrives, so an abort issued while the coroutine is suspended
        awaiting the network (the initial API call / thinking phase, or between
        sparse chunks) wouldn't take effect until the next chunk. Here each
        ``__anext__`` is raced against ``signal.wait()`` so the in-flight read is
        cancelled the moment abort fires.
        """
        stream_iter = stream.__aiter__()
        signal_task = asyncio.ensure_future(signal.wait())
        try:
            while not signal.is_set():
                event_task = asyncio.ensure_future(stream_iter.__anext__())
                done, _ = await asyncio.wait(
                    {event_task, signal_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task not in done:
                    # Abort won the race — cancel the pending read and stop.
                    event_task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await event_task
                    return
                try:
                    event = event_task.result()
                except StopAsyncIteration:
                    return
                yield event
        finally:
            if not signal_task.done():
                signal_task.cancel()
                with suppress(asyncio.CancelledError):
                    await signal_task

    async def _collect_steering(self) -> list[LLMMessage]:
        """Drain pending steering messages (queue + options callback).

        Refreshes the pending-queue display so the steering hint reflects what
        remains (and vanishes when the queue empties).
        """
        out: list[LLMMessage] = []
        if self.state.steering_queue and not self.state.steering_queue.is_empty():
            out.extend(await self.state.steering_queue.dequeue())
            await self.hooks.emit(
                QueueUpdateEvent(queue="steering", messages=self.state.steering_queue.snapshot())
            )
        if self.options.get_steering_messages is not None:
            out.extend(self.options.get_steering_messages())
        return out

    async def _collect_follow_up(self) -> list[LLMMessage]:
        """Drain pending follow-up messages (queue + options callback), refreshing the display."""
        out: list[LLMMessage] = []
        if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
            out.extend(await self.state.follow_up_queue.dequeue())
            await self.hooks.emit(
                QueueUpdateEvent(queue="followup", messages=self.state.follow_up_queue.snapshot())
            )
        if self.options.get_follow_up_messages is not None:
            out.extend(self.options.get_follow_up_messages())
        return out

    async def _loop(self, messages: list[LLMMessage], emit: EmitEvent, signal: AbortSignal) -> None:
        """Core agentic loop: stream LLM → execute tools → inject steering/follow-ups →
        repeat until done.

        Structured as two loops:
          * inner — runs while there are tool calls to answer *or* queued
            steering/follow-up messages to inject. Steering is re-polled at the end
            of every turn, so a steer that lands on a plain-text turn is picked up
            here with no special-casing.
          * outer — resumes the inner loop when follow-up messages arrive after the
            agent would otherwise stop.

        Args:
            messages: Conversation history to pass to the LLM.
            emit: Callback to emit engine events.
            signal: Abort signal to check for user-initiated cancellation.
        """
        await emit(AgentStartEvent())

        model_name = self.llm.model.name if self.llm is not None else "unknown"
        _log.debug("agent loop starting: model=%s", model_name)

        tool_calls: list[ToolCallContent] = []
        tool_results: list[ToolResultContent] = []
        end_reason: AgentEndReason = AgentEndReason.Completed

        async def inject(pending: list[LLMMessage]) -> None:
            """Surface queued steering/follow-up as real user turns in history."""
            for msg in pending:
                await emit(MessageStartEvent(message=msg))
                await emit(MessageEndEvent(message=msg))
                messages.append(msg)

        try:
            # The user may have steered while waiting for the turn to begin.
            pending: list[LLMMessage] = await self._collect_steering()
            stop = False

            while not stop:
                has_more_tool_calls = True
                while has_more_tool_calls or pending:
                    _log.debug("turn start: messages=%d", len(messages))
                    await emit(TurnStartEvent())
                    tool_results = []

                    # Inject queued steering/follow-up ahead of the assistant response.
                    if pending:
                        await inject(pending)
                        pending = []

                    # ── Mid-turn compaction / context refresh ──────────────────
                    # Called before every LLM inference so compaction can fire
                    # between tool iterations, not only at invoke() boundaries.
                    if self.options.transform_context is not None:
                        from tau.session.compaction import ThresholdCompactionStop

                        try:
                            messages = await self.options.transform_context(messages, signal)
                        except ThresholdCompactionStop:
                            # Threshold compaction fired mid-turn. Stop cleanly so the
                            # user can review the compacted context and continue.
                            await emit(TurnEndEvent(message=AssistantMessage(), tool_results=[]))
                            stop = True
                            break

                    message = AssistantMessage()
                    tool_calls.clear()
                    ctx_messages = list(messages)

                    if signal.is_set():
                        closing = AssistantMessage(stop_reason=StopReason.Abort)
                        await emit(MessageStartEvent(message=closing))
                        await emit(MessageEndEvent(message=closing))
                        messages.append(closing)
                        await emit(TurnEndEvent(message=closing, tool_results=tool_results))
                        stop = True
                        break

                    ctx = LLMContext(
                        messages=ctx_messages,
                        tools=self.state.tools,
                        system_prompt=self.state.system_prompt,
                    )

                    await emit(MessageStartEvent(message=message))
                    await self.hooks.emit(
                        BeforeProviderRequestEvent(
                            model=self.llm.model,
                            messages=ctx_messages,
                            options=self.llm.api.options,
                        )
                    )

                    stream = self.llm.stream(ctx)
                    _streaming_text: Any = None
                    _streaming_thinking: Any = None
                    async for event in self._iter_with_abort(stream, signal):
                            match event:
                                case ToolCallEndEvent(tool_call=tool_call):
                                    tool_calls.append(tool_call)
                                    message.contents.append(tool_call)
                                    # Surface the tool call to the UI as it streams and
                                    # mark the turn as having content, so an abort here is
                                    # treated as mid-stream (not a pre-stream undo).
                                    await emit(MessageUpdateEvent(message=message))
                                case TextDeltaEvent(text=text):
                                    if _streaming_text is None:
                                        from tau.message.types import TextContent

                                        _streaming_text = TextContent(content=text.content)
                                        message.contents.append(_streaming_text)
                                    else:
                                        _streaming_text.content += text.content
                                    await emit(MessageUpdateEvent(message=message))
                                case ThinkingDeltaEvent(thinking=thinking):
                                    if _streaming_thinking is None:
                                        from tau.message.types import ThinkingContent

                                        _streaming_thinking = ThinkingContent(
                                            content=thinking.content
                                        )
                                        message.contents.append(_streaming_thinking)
                                    else:
                                        _streaming_thinking.content += thinking.content
                                    await emit(MessageUpdateEvent(message=message))
                                case TextEndEvent(text=text):
                                    if _streaming_text is not None:
                                        _streaming_text.content = text.content
                                        _streaming_text = None
                                    else:
                                        message.contents.append(text)
                                case ThinkingEndEvent(thinking=thinking):
                                    if _streaming_thinking is not None:
                                        _streaming_thinking.content = thinking.content
                                        _streaming_thinking = None
                                    else:
                                        message.contents.append(thinking)
                                case ErrorEvent(reason=reason, error=error, kind=kind):
                                    message.stop_reason = reason
                                    message.error = error
                                    message.error_kind = kind
                                case EndEvent() as ev:
                                    message.stop_reason = ev.reason
                                    message.usage = Usage(
                                        input_tokens=ev.input_tokens,
                                        output_tokens=ev.output_tokens,
                                        cache_read_tokens=ev.cache_read_tokens,
                                        cache_write_tokens=ev.cache_write_tokens,
                                        cache_write_1h_tokens=ev.cache_write_1h_tokens,
                                    )

                    # If we broke out of the stream early due to abort, treat as abort
                    if signal.is_set() and message.stop_reason == StopReason.Stop:
                        message.stop_reason = StopReason.Abort

                    await self.hooks.emit(
                        AfterProviderResponseEvent(model=self.llm.model, response=message)
                    )

                    # ── Terminal stop reasons end the run outright ─────────────
                    if message.stop_reason == StopReason.Abort:
                        # Partial text gives the LLM context about what it was
                        # generating when interrupted, so keep it. But if the model
                        # had begun a tool call, drop the whole turn — an unfinished
                        # tool call that never ran has no matching result and would
                        # leave a dangling tool_use. Replace it with a clean marker.
                        if message.tool_calls():
                            message = AssistantMessage(stop_reason=StopReason.Abort)
                        await emit(MessageEndEvent(message=message))
                        messages.append(message)
                        end_reason = AgentEndReason.Aborted
                        await emit(TurnEndEvent(message=message, tool_results=tool_results))
                        stop = True
                        break

                    if message.stop_reason == StopReason.Error:
                        await emit(MessageEndEvent(message=message))
                        err_msg = (
                            message.error or f"Turn failed with reason: {message.stop_reason.value}"
                        )
                        _log.error("llm error: %s", err_msg)
                        end_reason = AgentEndReason.Error
                        await emit(AgentErrorEvent(error=err_msg))
                        await emit(TurnEndEvent(message=None, tool_results=tool_results))
                        stop = True
                        break

                    # Commit the assistant message and answer any tool calls it made.
                    await emit(MessageEndEvent(message=message))
                    messages.append(message)
                    has_more_tool_calls = False

                    if tool_calls:
                        tool_results = await self._execute_tool_calls(
                            tool_calls=tool_calls, emit=emit, signal=signal
                        )
                        tool_message = ToolMessage.from_results(tool_results)
                        await emit(MessageStartEvent(message=tool_message))
                        await emit(MessageEndEvent(message=tool_message))
                        messages.append(tool_message)

                        # If every tool signalled terminate, stop without another LLM call.
                        if tool_results and all(r.terminate for r in tool_results):
                            text = "\n".join(
                                (r.terminate_message or r.content)
                                for r in tool_results
                                if (r.terminate_message or r.content)
                            )
                            if text:
                                message = AssistantMessage.from_text(text)
                                await emit(MessageEndEvent(message=message))
                                messages.append(message)
                            await emit(TurnEndEvent(message=message, tool_results=tool_results))
                            stop = True
                            break

                        if signal.is_set():
                            end_reason = AgentEndReason.Aborted
                            # Drop the whole interrupted tool turn. The assistant
                            # tool-call message and its (possibly partial) tool-result
                            # message were already committed before/while tools ran;
                            # retract both from history and UI, leaving only a clean
                            # interrupt marker — as if the tool turn never happened.
                            await emit(MessageRollbackEvent(count=2))
                            del messages[-2:]
                            message = AssistantMessage(stop_reason=StopReason.Abort)
                            await emit(MessageStartEvent(message=message))
                            await emit(MessageEndEvent(message=message))
                            messages.append(message)
                            await emit(TurnEndEvent(message=message, tool_results=[]))
                            stop = True
                            break

                        has_more_tool_calls = True

                    await emit(TurnEndEvent(message=message, tool_results=tool_results))

                    if self.options.should_stop_after_turn and self.options.should_stop_after_turn(
                        message, tool_results
                    ):
                        stop = True
                        break

                    # Re-poll steering: a message queued during this turn is injected
                    # on the next inner iteration (so the loop keeps going after a
                    # plain-text turn when the user steered).
                    pending = await self._collect_steering()

                if stop:
                    break

                # Inner loop drained tool calls and steering. Pick up any follow-up
                # messages for a fresh inner pass; otherwise the agent is done.
                pending = await self._collect_follow_up()
                if not pending:
                    break
        except Exception as e:
            end_reason = AgentEndReason.Error
            await emit(AgentErrorEvent(error=str(e)))

        _log.debug("agent loop ended: reason=%s", end_reason.value)
        await emit(AgentEndEvent(messages=messages, reason=end_reason))

    async def run(self, ctx: AgentContext, signal: AbortSignal | None = None) -> None:
        """Apply context and start a fresh loop. Uses the provided signal or creates one."""
        self._signal = signal or asyncio.Event()
        self.state.is_streaming = True
        self.state.idle_event.clear()
        self.state.system_prompt = ctx.system_prompt
        self.state.tools = ctx.tools
        self._tools = {t.name: t for t in ctx.tools}
        try:
            await self._loop(ctx.messages, self.process_events, self._signal)
        finally:
            self.state.is_streaming = False
            self.state.idle_event.set()

    async def _inject_queued(
        self, queue_name: Literal["steering", "followup"], messages: list[LLMMessage]
    ) -> None:
        """Surface dequeued steering/follow-up messages as real conversation entries.

        Emits start/end for each so the UI renders them and ``state.messages``
        (the source of truth a continuation runs from) includes them — matching
        how the in-loop injection path behaves.
        """
        snapshot = (
            self.state.steering_queue.snapshot()
            if queue_name == "steering" and self.state.steering_queue
            else self.state.follow_up_queue.snapshot()
            if self.state.follow_up_queue
            else []
        )
        await self.hooks.emit(QueueUpdateEvent(queue=queue_name, messages=snapshot))
        for msg in messages:
            await self.process_events(MessageStartEvent(message=msg))
            await self.process_events(MessageEndEvent(message=msg))

    async def run_continue(self, signal: AbortSignal | None = None) -> None:
        """Resume an idle engine from its current message history,
        draining queued steering/follow-up first.

        Args:
            signal: Abort signal to thread through the continuation turn.

        Raises:
            RuntimeError: If the engine is currently streaming or has no messages.
        """
        from tau.agent.types import AgentContext

        if self.state.is_streaming:
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        if not self.state.messages:
            # Edge case: session was reset but follow-up messages were enqueued before any LLM turn.
            if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                follow_up_messages = await self.state.follow_up_queue.dequeue()
                await self.hooks.emit(
                    QueueUpdateEvent(
                        queue="followup",
                        messages=self.state.follow_up_queue.snapshot(),
                    )
                )
                await self.run(
                    AgentContext(
                        system_prompt=self.state.system_prompt or "",
                        messages=follow_up_messages,
                    ),
                    signal=signal,
                )
                return
            raise RuntimeError("No messages to continue from")

        last_message = self.state.messages[-1]
        match last_message.role:
            case Role.ASSISTANT:
                if self.state.steering_queue and not self.state.steering_queue.is_empty():
                    steering_messages = await self.state.steering_queue.dequeue()
                    await self._inject_queued("steering", steering_messages)
                    await self.run(
                        AgentContext(
                            system_prompt=self.state.system_prompt or "",
                            messages=list(self.state.messages),
                        ),
                        signal=signal,
                    )
                    return

                if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                    follow_up_messages = await self.state.follow_up_queue.dequeue()
                    await self._inject_queued("followup", follow_up_messages)
                    await self.run(
                        AgentContext(
                            system_prompt=self.state.system_prompt or "",
                            messages=list(self.state.messages),
                        ),
                        signal=signal,
                    )
                    return

                raise RuntimeError("Cannot continue from message role: assistant")
            case _:
                pass

        await self._loop_continue()

    async def _loop_continue(self) -> None:
        """Re-enter the loop with existing state.messages
        (used when last message is a tool result).
        """
        self._signal = asyncio.Event()  # standalone re-entry gets its own fresh signal
        self.state.is_streaming = True
        self.state.idle_event.clear()
        try:
            await self._loop(self.state.messages, self.process_events, self._signal)
        finally:
            self.state.is_streaming = False
            self.state.idle_event.set()
