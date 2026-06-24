from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tau.agent.types import AgentConfig, AgentContext, AgentPhase, ContextUsage, PromptOptions
from tau.hooks.engine import CompactionReason as _CompactionReason
from tau.hooks.engine import MessageEndEvent, MessageRollbackEvent, SavePointEvent, SettledEvent
from tau.hooks.service import Hooks
from tau.message.types import (
    AssistantMessage,
    LLMMessage,
    ToolMessage,
    UserMessage,
)
from tau.message.utils import strip_unusable_trailing_assistant
from tau.session.compaction import ThresholdCompactionStop as _ThresholdCompactionStop
from tau.session.utils import to_llm_messages as _to_llm_messages
from tau.tool.types import ToolInvocation, ToolResult

_log = logging.getLogger(__name__)

_TOOL_CAP_BYTES = 50 * 1024   # 50 KB — DEFAULT_MAX_BYTES
_TOOL_CAP_LINES = 2000         # DEFAULT_MAX_LINES
_TOOL_LINE_CAP_BYTES = 2 * 1024  # 2 KB — max bytes for a single line


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"

if TYPE_CHECKING:
    from tau.engine.service import Engine
    from tau.runtime.service import Runtime
    from tau.session.compaction import CompactionPreparation
    from tau.session.manager import SessionManager


class Agent:
    """
    High-level agent session tying together Engine and SessionManager.

    Call `invoke()` to run a user turn. The session persists each message
    and tracks token usage.
    """

    def __init__(
        self,
        engine: Engine,
        session_manager: SessionManager,
        config: AgentConfig,
        hooks: Hooks | None = None,
    ) -> None:
        self._engine = engine
        self._session_manager = session_manager
        self._config = config
        self._system_prompt: str = config.system_prompt
        self._context_tokens: int = 0
        self._context_window: int = config.context_window
        self._runtime: Runtime | None = None
        self.hooks = hooks or Hooks()

        self._phase: AgentPhase = AgentPhase.IDLE
        self._signal: asyncio.Event = asyncio.Event()
        self._compaction_failures: int = 0
        self._overflow_recovery_attempted: bool = False
        self._engine.options.before_tool_call = self._before_tool_call
        self._engine.options.after_tool_call = self._after_tool_call
        self._engine.options.transform_context = self._transform_context

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    @property
    def cwd(self) -> Path:
        """Get the current working directory."""
        return self._config.cwd

    @property
    def session_manager(self) -> SessionManager:
        """Get the session manager instance."""
        return self._session_manager

    def is_idle(self) -> bool:
        """Check if the agent is idle (not processing)."""
        return self._engine.is_idle

    def has_pending_messages(self) -> bool:
        """Check if there are pending messages in the queue."""
        return self._engine.has_pending_messages()

    def abort(self) -> None:
        """Request abort of current operation."""
        self._signal.set()

    def shutdown(self) -> None:
        """Shutdown the agent."""
        self._signal.set()

    def update_context_tokens(self) -> None:
        """Recalculate context token usage."""
        from tau.session.compaction import estimate_context_tokens

        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        usage = estimate_context_tokens(llm_messages)
        self._context_tokens = usage.tokens

    def get_context_usage(self) -> ContextUsage | None:
        """Get current context token usage and limits."""
        self.update_context_tokens()
        percent = (
            (self._context_tokens / self._context_window * 100) if self._context_window else None
        )
        return ContextUsage(
            tokens=self._context_tokens,
            context_window=self._context_window,
            percent=percent,
        )

    def get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        return self._system_prompt

    async def wait_for_idle(self) -> None:
        """Wait until the agent becomes idle."""
        await self._engine.wait_for_idle()

    async def new_session(self) -> None:
        """Create a new session."""
        if self._runtime is not None:
            await self._runtime.new_session()

    async def fork(self, entry_id: str) -> None:
        """Fork a session from a specific entry."""
        if self._runtime is not None:
            await self._runtime.fork_session(entry_id)

    async def switch_session(self, session_file: Path) -> None:
        """Switch to a different session."""
        if self._runtime is not None:
            await self._runtime.resume_session(session_file)

    # -------------------------------------------------------------------------
    # Engine-level tool hooks (pass-through)
    # -------------------------------------------------------------------------

    async def _before_tool_call(
        self,
        invocation: ToolInvocation,
        signal: asyncio.Event | None,
    ) -> ToolInvocation | None:
        return invocation

    async def _after_tool_call(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        signal: asyncio.Event | None,
    ) -> ToolResult | None:
        """Cap oversized tool output before it enters the context window.

        Hard cap on tool output size 50 KB / 2000-line 
        Head-truncation keeps the first N lines/bytes; a trailing marker
        reports how much was omitted and the total size.
        """
        content = result.content
        raw = content.encode("utf-8", errors="replace")
        total_bytes = len(raw)
        lines = content.split("\n")
        total_lines = len(lines)

        if total_bytes <= _TOOL_CAP_BYTES and total_lines <= _TOOL_CAP_LINES:
            return result

        # Cap individual lines that would consume the entire budget on their own
        # (e.g. minified JS). Truncate each line to _TOOL_LINE_CAP_BYTES.
        capped_lines: list[str] = []
        for line in lines:
            lb = len(line.encode("utf-8", errors="replace"))
            if lb > _TOOL_LINE_CAP_BYTES:
                buf = line.encode("utf-8", errors="replace")[:_TOOL_LINE_CAP_BYTES]
                # Walk back to a valid UTF-8 boundary
                while buf and (buf[-1] & 0xC0) == 0x80:
                    buf = buf[:-1]
                suffix = f" …[line truncated: {_fmt_size(lb)} → {_fmt_size(_TOOL_LINE_CAP_BYTES)}]"
                capped_lines.append(buf.decode("utf-8", errors="replace") + suffix)
            else:
                capped_lines.append(line)
        lines = capped_lines

        kept: list[str] = []
        byte_count = 0
        for i, line in enumerate(lines):
            if i >= _TOOL_CAP_LINES:
                break
            enc = len(line.encode("utf-8", errors="replace")) + (1 if i > 0 else 0)
            if byte_count + enc > _TOOL_CAP_BYTES:
                break
            kept.append(line)
            byte_count += enc

        omitted = total_bytes - byte_count
        kept.append(
            f"[truncated: {_fmt_size(omitted)} omitted — {_fmt_size(total_bytes)} total,"
            f" showing first {len(kept)} lines / {_fmt_size(byte_count)}]"
        )
        return ToolResult(
            id=result.id,
            content="\n".join(kept),
            is_error=result.is_error,
            metadata=result.metadata,
            terminate=result.terminate,
            terminate_message=result.terminate_message,
        )

    async def _transform_context(
        self,
        messages: list[LLMMessage],
        signal: asyncio.Event | None,
    ) -> list[LLMMessage]:
        """Called before every LLM inference in the engine loop.

        Runs a compaction check so it can fire between tool iterations
        (not only at invoke() boundaries), then rebuilds the message list
        from the current session so the engine always sees up-to-date
        compacted history.
        """
        threshold_stop = await self._check_compaction()
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        result = strip_unusable_trailing_assistant(llm_messages, self._session_manager)
        if threshold_stop:
            raise _ThresholdCompactionStop()
        return result

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _on_message_end(self, event: MessageEndEvent) -> None:
        """Persist an incoming message to the session and track token usage."""
        message = event.message
        if message is None:
            return
        match message:
            case AssistantMessage():
                total = message.usage.input_tokens + message.usage.output_tokens
                if total:
                    self._context_tokens = total
                self._session_manager.append_message(message)
            case ToolMessage():
                self._session_manager.append_message(message)
            case _:
                pass

    async def _on_message_rollback(self, event: MessageRollbackEvent) -> None:
        """Retract the last ``event.count`` persisted messages from the session.

        Fired when an interrupted tool turn is dropped: the assistant tool-call
        message and its tool-result message were already written, so remove them
        to keep the session consistent with what the engine replays.
        """
        for _ in range(event.count):
            if not self._session_manager.remove_last_message():
                break

    # -------------------------------------------------------------------------
    # Compaction
    # -------------------------------------------------------------------------

    async def compact(self, custom_instructions: str | None = None) -> bool:
        """Manually trigger context compaction. Returns True if compaction ran."""
        from tau.session.compaction import prepare_compaction

        entries = self._session_manager.get_branch()
        preparation = prepare_compaction(entries, self._config.compaction)
        if preparation is None:
            return False
        await self._apply_compaction(
            preparation, entries, manual=True, custom_instructions=custom_instructions,
            reason=_CompactionReason.Manual,
        )
        return True

    async def _apply_compaction(
        self,
        preparation: CompactionPreparation,
        entries: list,
        manual: bool,
        custom_instructions: str | None = None,
        reason: _CompactionReason = _CompactionReason.Manual,
    ) -> None:
        """Run a prepared compaction, persist the summary, and emit the end event."""
        from tau.hooks.engine import CompactionEndEvent

        will_retry = reason == _CompactionReason.Overflow
        result, from_extension = await self._run_compaction(
            preparation, entries, manual=manual, custom_instructions=custom_instructions,
            reason=reason, will_retry=will_retry,
        )
        self._session_manager.append_compaction(
            summary=result.summary,
            first_kept_entry_id=result.first_kept_entry_id,
            tokens_before=result.tokens_before,
        )
        self._compaction_failures = 0
        await self.hooks.emit(
            CompactionEndEvent(
                manual=manual,
                tokens_before=result.tokens_before,
                summary_length=len(result.summary),
                from_extension=from_extension,
                reason=reason,
                will_retry=will_retry,
            )
        )

    def _latest_model_change_timestamp(self) -> float | None:
        """Timestamp of the most recent model-change entry in the active branch, if any."""
        from tau.session.types import ModelChangeEntry

        for entry in reversed(self._session_manager.get_branch()):
            if isinstance(entry, ModelChangeEntry):
                return entry.timestamp
        return None

    async def _check_compaction(self) -> bool:
        """Auto-compact if context usage exceeds the threshold. Circuit-breaks after 3 failures.

        Returns True if threshold compaction ran (caller should stop the current turn),
        False otherwise (overflow-forced compaction or no compaction needed).
        """
        from tau.session.compaction import (
            estimate_context_tokens,
            is_silent_overflow,
            latest_compaction_timestamp,
            prepare_compaction,
            should_compact,
        )

        if self._compaction_failures >= 3:
            return False

        settings = self._config.compaction
        if not settings.enabled:
            return False

        entries = self._session_manager.get_branch()
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)

        usage = estimate_context_tokens(llm_messages)

        # "Silent" overflow: some providers accept an over-limit prompt and return a
        # successful response (z.ai) or truncate the input and stop with no output
        # (Xiaomi MiMo) instead of erroring. The threshold check can miss these, so
        # force compaction when the last response shows the symptom.
        last = self._session_manager.find_last_assistant_message()

        # Model-switch guard: if the last assistant message is older than the most recent
        # model change, it came from a different model. Treating its usage/overflow data as
        # a signal for the new model is unreliable (context windows differ), so skip.
        model_change_ts = self._latest_model_change_timestamp()
        if model_change_ts is not None and last is not None and last.timestamp <= model_change_ts:
            return False

        forced = last is not None and is_silent_overflow(last, self._context_window)

        if not forced:
            if not should_compact(usage.tokens, self._context_window, settings):
                return False
            # Stale-anchor guard: right after a compaction the kept messages still carry
            # pre-compaction usage on their anchor, which would re-trigger compaction every
            # turn. Skip if the usage anchor predates the latest compaction boundary.
            if usage.last_usage_index is not None:
                anchor = llm_messages[usage.last_usage_index]
                comp_ts = latest_compaction_timestamp(entries)
                if comp_ts is not None and getattr(anchor, "timestamp", 0.0) <= comp_ts:
                    return False

        preparation = prepare_compaction(entries, settings)
        if preparation is None:
            return False

        try:
            await self._apply_compaction(
                preparation, entries, manual=False,
                reason=_CompactionReason.Overflow if forced else _CompactionReason.Threshold,
            )
            # Threshold compaction: caller should stop the turn; user resumes manually.
            # Forced (silent overflow) compaction: caller should continue — the LLM
            # never got a usable response, so there's nothing for the user to resume from.
            return not forced
        except Exception:
            self._compaction_failures += 1
            _log.exception("Auto-compaction failed")
            return False

    async def _try_overflow_recovery(self) -> bool:
        """If the last turn died with a context-overflow error, compact once and signal a retry.

        Drops the error message so it isn't kept or used as a stale anchor, compacts the
        history, and lets the caller re-run the turn. Bounded to one attempt per turn so a
        session that overflows even after compaction fails cleanly.
        """
        from tau.inference.utils import ErrorKind
        from tau.session.compaction import prepare_compaction

        last = self._session_manager.find_last_assistant_message()
        if last is None or last.error_kind != ErrorKind.CONTEXT_OVERFLOW:
            return False

        # Model-switch guard: the overflow error is from a different model if it predates
        # the most recent model-change entry. Skip recovery — the new model may handle the
        # context fine, and compacting based on a stale signal wastes history.
        model_change_ts = self._latest_model_change_timestamp()
        if model_change_ts is not None and last.timestamp <= model_change_ts:
            return False

        if self._overflow_recovery_attempted:
            self._notify(
                "Context overflow recovery failed after compaction. "
                "Reduce context or switch to a larger-context model."
            )
            return False
        self._overflow_recovery_attempted = True

        # Drop the error assistant message — it has no usable content and would otherwise
        # anchor stale usage / be re-sent on retry.
        self._session_manager.remove_last_message()

        entries = self._session_manager.get_branch()
        preparation = prepare_compaction(entries, self._config.compaction)
        if preparation is None:
            return False
        try:
            await self._apply_compaction(
                preparation, entries, manual=False, reason=_CompactionReason.Overflow
            )
        except Exception:
            self._compaction_failures += 1
            _log.exception("Overflow-triggered compaction failed")
            return False
        return True

    def _notify(self, message: str) -> None:
        """Surface a message to the UI if a runtime/UI is wired up."""
        if self._runtime is None:
            return
        from tau.extensions.context import ExtensionContext

        ctx = ExtensionContext.from_runtime(self._runtime)
        if ctx.ui is not None:
            ctx.ui.notify(message)

    async def _run_compaction(
        self,
        preparation: CompactionPreparation,
        entries: list,
        manual: bool,
        custom_instructions: str | None = None,
        reason: _CompactionReason = _CompactionReason.Manual,
        will_retry: bool = False,
    ) -> tuple:
        """Emit before_compaction (allowing interception), then run the default algorithm.

        Returns (CompactionResult, from_extension: bool).
        Extensions may cancel (raises RuntimeError) or supply a custom CompactionResult.
        Exceptions in before_compaction handlers are swallowed — first non-error result wins,
        consistent with error-fallthrough behaviour.
        """
        from tau.hooks.engine import (
            BeforeCompactionEvent,
            BeforeCompactionResult,
            CompactionStartEvent,
        )
        from tau.session.compaction import compact as _compact

        before_results = await self.hooks.emit(
            BeforeCompactionEvent(
                preparation=preparation,
                entries=entries,
                manual=manual,
                reason=reason,
                will_retry=will_retry,
            )
        )

        for res in before_results:
            if not isinstance(res, BeforeCompactionResult):
                continue
            if res.cancel:
                raise RuntimeError("Compaction cancelled by extension")
            if res.compaction is not None:
                return res.compaction, True

        await self.hooks.emit(
            CompactionStartEvent(manual=manual, reason=reason, will_retry=will_retry)
        )
        result = await _compact(
            preparation, self._engine.llm, custom_instructions=custom_instructions
        )  # type: ignore[arg-type]
        return result, False

    # -------------------------------------------------------------------------
    # Core turn entry point
    # -------------------------------------------------------------------------

    async def invoke(self, text: str, options: PromptOptions | None = None) -> None:
        """Run one user turn."""
        if self._phase != AgentPhase.IDLE:
            raise RuntimeError(
                f"Agent is busy (phase={self._phase!r}). Wait for the current operation to finish."
            )

        opts = options or PromptOptions()

        user_message = UserMessage.with_media(
            text,
            list(opts.images) if opts.images else None,
            list(opts.audio) if opts.audio else None,
            list(opts.video) if opts.video else None,
        )
        self._session_manager.append_message(user_message, meta=opts.meta)

        self._overflow_recovery_attempted = False
        self._phase = AgentPhase.TURN
        try:
            while True:
                ctx = self._build_turn_context()
                self._signal = asyncio.Event()
                self._engine.llm.api.options.signal = self._signal
                try:
                    await self._run(ctx)
                    break
                except RuntimeError:
                    # On a context-overflow error, compact and retry the turn once.
                    if await self._try_overflow_recovery():
                        continue
                    raise
        finally:
            self._phase = AgentPhase.IDLE

        await self.hooks.emit(SavePointEvent())

        await self._check_compaction()

        if not self._engine.has_pending_messages():
            await self.hooks.emit(SettledEvent())

    def _build_turn_context(self) -> AgentContext:
        """Build the LLM context for a turn from the current (possibly compacted) session."""
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        llm_messages = strip_unusable_trailing_assistant(llm_messages, self._session_manager)
        return AgentContext(
            system_prompt=self._system_prompt,
            messages=llm_messages,
            tools=self._engine.tools,
        )

    async def _run(self, ctx: AgentContext) -> None:
        unsubscribe = self.hooks.register(
            "message_end",
            lambda event: self._on_message_end(event),
        )
        unsubscribe_rollback = self.hooks.register(
            "message_rollback",
            lambda event: self._on_message_rollback(event),
        )
        try:
            await self._engine.run(ctx, signal=self._signal)
        finally:
            unsubscribe()
            unsubscribe_rollback()

        error = self._engine.state.error_message
        if error is not None:
            raise RuntimeError(f"Agent failed: {error}.")
