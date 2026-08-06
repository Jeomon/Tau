from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tau.agent.types import AgentConfig, AgentPhase, ContextUsage, PromptOptions
from tau.engine.types import EngineContext
from tau.hooks.engine import CompactionReason as _CompactionReason
from tau.hooks.engine import (
    MessageEndEvent,
    MessageRollbackEvent,
    SavePointEvent,
    SettledEvent,
    ToolCallEvent,
    ToolCallEventResult,
)
from tau.hooks.service import Hooks
from tau.message.types import (
    AssistantMessage,
    LLMMessage,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)
from tau.message.utils import strip_unusable_trailing_assistant
from tau.session.compaction import CompactionSettings
from tau.session.types import SessionContext
from tau.session.utils import to_llm_messages as _to_llm_messages
from tau.tool.types import ToolInvocation, ToolResult
from tau.utils.format import human_size as _fmt_size

_log = logging.getLogger(__name__)


class _CompactionCancelledError(RuntimeError):
    """Raised after an extension cancels compaction."""


_TOOL_CAP_BYTES = 50 * 1024  # 50 KB — DEFAULT_MAX_BYTES
_TOOL_CAP_LINES = 2000  # DEFAULT_MAX_LINES
_TOOL_LINE_CAP_BYTES = 2 * 1024  # 2 KB — max bytes for a single line


if TYPE_CHECKING:
    from tau.engine.service import Engine
    from tau.runtime.service import Runtime
    from tau.session.compaction import CompactionPreparation
    from tau.session.manager import SessionManager


def _media_modalities() -> tuple[dict[type, Any], dict[str, Any]]:
    """Map each media content type, and each tool-result media slot, to its modality.

    Kept in one place so a new modality only has to be added here: everything
    the model cannot accept is filtered by the same code path.
    """
    from tau.inference.model.types import Modality
    from tau.message.types import AudioContent, FileContent, ImageContent, VideoContent

    by_type = {
        ImageContent: Modality.Image,
        AudioContent: Modality.Audio,
        VideoContent: Modality.Video,
        FileContent: Modality.File,
    }
    # ToolResultContent carries media in dedicated fields rather than in
    # `contents`; it has no `file` slot today, so this is deliberately shorter.
    by_slot = {
        "image": Modality.Image,
        "audio": Modality.Audio,
        "video": Modality.Video,
    }
    return by_type, by_slot


def _without_unsupported_media(
    message: LLMMessage, supported: set[Any], model_name: str
) -> LLMMessage | None:
    """Return a copy of ``message`` with unusable media replaced by a note.

    ``None`` means the message holds nothing the model rejects and can be
    reused as-is. Copies are shallow and made only where media was found, so
    untouched messages are never duplicated.
    """
    import dataclasses

    from tau.message.types import TextContent, ToolResultContent

    contents = getattr(message, "contents", None)
    if not contents:
        return None

    by_type, by_slot = _media_modalities()

    def note(modality: Any) -> str:
        return (
            f"[{modality.value.capitalize()} omitted: "
            f"{model_name} does not accept {modality.value} input.]"
        )

    changed = False
    rebuilt: list[Any] = []
    for item in contents:
        # A user message (or an attachment) carrying the media directly.
        modality = next((m for t, m in by_type.items() if isinstance(item, t)), None)
        if modality is not None and modality not in supported:
            rebuilt.append(TextContent(content=note(modality)))
            changed = True
            continue

        # A tool result carrying media alongside its text, e.g. a screenshot.
        if isinstance(item, ToolResultContent):
            dropped = [
                mod
                for slot, mod in by_slot.items()
                if getattr(item, slot, None) is not None and mod not in supported
            ]
            if dropped:
                text = item.content or ""
                suffix = "\n".join(note(m) for m in dropped)
                rebuilt.append(
                    dataclasses.replace(
                        item,
                        **{slot: None for slot, mod in by_slot.items() if mod in dropped},
                        content=f"{text}\n{suffix}" if text else suffix,
                    )
                )
                changed = True
                continue

        rebuilt.append(item)

    if not changed:
        return None
    return dataclasses.replace(message, contents=rebuilt)


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
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()
        self._signal: asyncio.Event = asyncio.Event()
        # invoke() swaps in a fresh _signal for each retry/continuation, so an
        # abort issued between swaps (e.g. during compaction or save-point
        # hooks) would be silently dropped without this persistent flag.
        self._abort_requested: bool = False
        self._compaction_failures: int = 0
        self._compaction_circuit_notified: bool = False
        self._overflow_recovery_attempted: bool = False
        self._engine.options.before_tool_call = self._before_tool_call
        self._engine.options.after_tool_call = self._after_tool_call
        self._engine.options.transform_context = self._transform_context
        self._engine.options.ephemeral_injection = self._ephemeral_injection
        # Engine._loop calls transform_context() then ephemeral_injection() back
        # to back on every turn, and nothing in between touches session state —
        # so the SessionContext transform_context just built is still valid.
        # Stashed here so ephemeral_injection() doesn't redundantly rebuild it
        # (walk the whole branch chain and rescan every entry a second time).
        # Cleared immediately after use so a call to ephemeral_injection()
        # without a preceding transform_context() this turn — not how Agent
        # wires them, but Engine's callback slots are independently
        # configurable — safely falls back to building its own instead of
        # serving a stale context from a previous turn.
        self._pending_session_ctx: SessionContext | None = None

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

    @property
    def phase(self) -> AgentPhase:
        """Return the current observable agent phase."""
        return self._phase

    @property
    def streaming_message(self) -> AssistantMessage | None:
        """Return the partial assistant message currently being streamed."""
        return self._engine.state.streaming_message

    @property
    def pending_tool_call_ids(self) -> frozenset[str]:
        """Return a snapshot of tool calls that have not finished."""
        return frozenset(self._engine.state.pending_tool_calls)

    @property
    def error_message(self) -> str | None:
        """Return the most recent engine error message."""
        return self._engine.state.error_message

    @property
    def queued_messages(self) -> dict[str, list[LLMMessage]]:
        """Return snapshots of the steering and follow-up queues."""
        state = self._engine.state
        return {
            "steering": state.steering_queue.snapshot() if state.steering_queue else [],
            "followup": state.follow_up_queue.snapshot() if state.follow_up_queue else [],
        }

    def is_idle(self) -> bool:
        """Return whether the complete agent invocation lifecycle is idle."""
        return self._phase is AgentPhase.IDLE

    def has_pending_messages(self) -> bool:
        """Check if there are pending messages in the queue."""
        return self._engine.has_pending_messages()

    def abort(self) -> None:
        """Request abort of current operation."""
        self._abort_requested = True
        self._signal.set()

    def shutdown(self) -> None:
        """Shutdown the agent."""
        self._abort_requested = True
        self._signal.set()

    def update_context_tokens(self) -> None:
        """Recalculate context token usage."""
        from tau.session.compaction import estimate_context_tokens, latest_compaction_timestamp

        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        usage = estimate_context_tokens(llm_messages)
        # Stale-anchor guard (mirrors _check_compaction): right after a
        # compaction the kept messages still carry pre-compaction usage on
        # their anchor, which would keep reporting the pre-compaction context
        # size until the next real response lands. Fall back to a from-scratch
        # estimate of the effective (summary + kept) message list instead.
        if usage.last_usage_index is not None:
            anchor = llm_messages[usage.last_usage_index]
            comp_ts = latest_compaction_timestamp(self._session_manager.get_branch())
            if comp_ts is not None and getattr(anchor, "timestamp", 0.0) <= comp_ts:
                usage = estimate_context_tokens(llm_messages, ignore_usage=True)
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
        """Wait for the active invocation, including post-run processing, to finish."""
        await self._idle_event.wait()

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
    # Engine-level tool hooks
    # -------------------------------------------------------------------------

    async def _before_tool_call(
        self,
        invocation: ToolInvocation,
        signal: asyncio.Event | None,
    ) -> ToolInvocation | ToolResultContent | None:
        """Emit ``tool_call`` and honour the first interceptable result.

        This is the only pre-execution gate: returning a ``ToolResultContent``
        cancels the call (``engine/service.py`` turns it straight into the tool
        result), while returning a rewritten invocation changes what runs.
        Handlers see the invocation *after* ``prepare_arguments``, so what a
        permission gate inspects is what the tool actually receives.

        ``block`` wins over ``params``: a handler that asks for both is denying,
        and silently running rewritten params would invert its intent.
        """
        results = await self.hooks.emit(
            ToolCallEvent(
                tool_call_id=invocation.id,
                tool_name=invocation.name,
                input=invocation.params,
            )
        )

        for res in results:
            if not isinstance(res, ToolCallEventResult):
                continue
            if res.block:
                return ToolResultContent(
                    id=invocation.id,
                    is_error=True,
                    content=res.reason or f"Tool call '{invocation.name}' blocked by an extension.",
                    metadata={"blocked_by_extension": True},
                )
            if res.params is not None:
                invocation.params = res.params

        return invocation

    async def _after_tool_call(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        signal: asyncio.Event | None,
    ) -> ToolResult | None:
        """
        Cap oversized tool output before it enters the context window.

        Hard cap on tool output size 50 KB / 2000-line
        Head-truncation keeps the first N lines/bytes; a trailing marker
        reports how much was omitted and the total size.

        Images a tool produced are bounded here too — see
        ``_bound_result_images``.
        """
        self._bound_result_images(result)

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
            # Carry the media over: truncating *text* must not delete the
            # screenshot a tool returned alongside it. Rebuilding without these
            # dropped the image whenever the same result also overran the text
            # cap — e.g. a browser tool returning a page dump plus a capture.
            image=result.image,
            audio=result.audio,
            video=result.video,
        )

    def _bound_result_images(self, result: ToolResult) -> None:
        """Resize images a tool produced before they enter the context window.

        Providers validate every image in a request, not just the newest one:
        Anthropic drops its per-image cap from 8000px to 2000px once a request
        carries many images, so an oversized screenshot that was accepted early
        in a session starts failing *every* later request. The image is written
        to the transcript, so it comes back on reload — that wedges the session
        permanently rather than for a turn, and only starting a new one clears
        it. `read`'s byte limit is not a substitute: a 3 MB PNG can still be
        8000px wide.

        Only tool results are handled here. Pasted images already go through
        `process_image` in the interactive input handler.
        """
        image = result.image
        if image is None or not image.images:
            return

        from tau.utils.image_processing import process_image

        settings = getattr(getattr(self, "_engine", None), "_settings", None)
        auto_resize = settings.get_image_auto_resize() if settings is not None else True

        processed: list[Any] = []
        note: str | None = None
        for item in image.images:
            # URLs are passed through by image_to_base64 with nothing to decode.
            if isinstance(item, str) and item.startswith("http"):
                processed.append(item)
                continue
            try:
                data = base64.b64decode(item) if isinstance(item, str) else item
                out = process_image(data, auto_resize=auto_resize)
            except Exception:
                # The tool already produced this image and the failure may just
                # be an unavailable backend, so pass it through rather than
                # silently deleting tool output.
                processed.append(item)
                continue
            processed.append(base64.b64encode(out.data).decode())
            note = note or out.dimension_note()

        image.images = processed
        # Keep a note the tool set itself; ours only fills the gap.
        if note and not image.dimension_note:
            image.dimension_note = note

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
        await self._check_compaction()
        session_ctx = self._session_manager.build_session_context()
        self._pending_session_ctx = session_ctx
        llm_messages = _to_llm_messages(session_ctx.messages)
        llm_messages = self._drop_unsupported_media(llm_messages)
        return strip_unusable_trailing_assistant(llm_messages, self._session_manager)

    def _drop_unsupported_media(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """Replace media blocks with a note when the active model can't accept them.

        `read` refuses to load an image on a text-only model, but nothing stops
        media already in history from outliving the model that accepted it:
        switch models with `/model`, and every subsequent request still carries
        it. Providers reject the whole request, so the turn fails, and it fails
        again next turn because the media is in the transcript — the session is
        stuck until it is started over. A wrong `input` entry in a model catalog
        produces the same loop without any switching.

        Covers every modality a message can carry (image, audio, video, file),
        not just images: a text-only model is equally unable to accept an audio
        clip a tool returned, and wedges the session the same way.

        Only the outgoing copy is touched. `_to_llm_messages` rebuilds this list
        from the session on every turn, so the stored transcript keeps the media
        and switching back to a capable model restores it.
        """
        engine = getattr(self, "_engine", None)
        model = getattr(getattr(engine, "llm", None), "model", None)
        # Unknown capabilities: leave the request alone rather than degrade it.
        if model is None or not getattr(model, "input", None):
            return messages

        supported = set(model.input)
        by_type, _ = _media_modalities()
        # Nothing to strip when the model accepts every modality a message can
        # hold — the common case, so it costs one set comparison per turn.
        if set(by_type.values()) <= supported:
            return messages

        name = getattr(model, "name", None) or "the active model"
        out: list[LLMMessage] = []
        for message in messages:
            replacement = _without_unsupported_media(message, supported, name)
            out.append(replacement if replacement is not None else message)
        return out

    async def _ephemeral_injection(self) -> list[UserMessage]:
        """Collect per-turn ephemeral messages from extensions via the "context" hook.

        Called before every LLM inference (see Engine._run). Results are appended
        to that single request's context only — never persisted to the session —
        so extensions can keep the model up to date on live state (e.g. a todo
        list) without that state needing to survive compaction.
        """
        from tau.hooks.engine import ContextEvent, ContextEventResult

        # Reuse the SessionContext transform_context() just built this same
        # turn (see the comment on self._pending_session_ctx in __init__)
        # instead of walking the whole branch chain and rescanning every
        # entry a second time.
        session_ctx = self._pending_session_ctx
        self._pending_session_ctx = None
        if session_ctx is None:
            session_ctx = self._session_manager.build_session_context()
        results = await self.hooks.emit(ContextEvent(messages=list(session_ctx.messages)))
        ephemeral: list[UserMessage] = []
        for result in results:
            if isinstance(result, ContextEventResult):
                ephemeral.extend(result.ephemeral_messages)
        # Engine._run appends these *after* transform_context has run, so they
        # would otherwise be the one way media the model cannot accept still
        # reaches the provider — an extension injecting a screenshot would wedge
        # a text-only model exactly as stored history used to.
        return self._drop_unsupported_media(ephemeral)  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _on_message_end(self, event: MessageEndEvent) -> None:
        """Persist an incoming message to the session and track token usage."""
        message = event.message
        if message is None:
            return
        # append_message() now always does a full merge-and-rewrite of the
        # session file under a FileLock (not a cheap append) so two managers
        # on the same session file don't clobber each other's entries. That
        # must not run on the event loop thread — same reasoning as
        # _on_message_rollback's to_thread use below, and this fires far more
        # often (every assistant/tool/user message, not just on abort).
        if not isinstance(message, AssistantMessage | ToolMessage | UserMessage):
            return
        if isinstance(message, AssistantMessage):
            from tau.session.compaction import effective_usage_tokens

            total = effective_usage_tokens(message.usage)
            if total:
                self._context_tokens = total
        await asyncio.to_thread(self._session_manager.append_message, message)

    async def _on_message_rollback(self, event: MessageRollbackEvent) -> None:
        """Retract the last ``event.count`` persisted messages from the session.

        Fired when an interrupted tool turn is dropped: the assistant tool-call
        message and its tool-result message were already written, so remove them
        to keep the session consistent with what the engine replays.
        """

        def _remove_up_to(count: int) -> None:
            for _ in range(count):
                if not self._session_manager.remove_last_message():
                    break

        # remove_last_message() rewrites the whole session file synchronously
        # once it's already flushed (the normal case) — this fires on every
        # abort of an in-flight tool call, a common interactive action, not a
        # rare one, so it must not freeze the TUI for however long that
        # rewrite takes on a long session.
        await asyncio.to_thread(_remove_up_to, event.count)

    # -------------------------------------------------------------------------
    # Compaction
    # -------------------------------------------------------------------------

    async def compact(self, custom_instructions: str | None = None) -> bool:
        """Manually trigger context compaction. Returns True if compaction ran."""
        from tau.session.compaction import prepare_compaction

        entries = self._session_manager.get_branch()
        preparation = prepare_compaction(entries, self._current_compaction_settings())
        if preparation is None:
            return False
        await self._apply_compaction(
            preparation,
            entries,
            manual=True,
            custom_instructions=custom_instructions,
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
        from tau.hooks.engine import CompactionEndEvent, CompactionFailureEvent

        will_retry = reason == _CompactionReason.Overflow
        previous_phase = self._phase
        self._phase = AgentPhase.COMPACTION
        try:
            result, from_extension = await self._run_compaction(
                preparation,
                entries,
                manual=manual,
                custom_instructions=custom_instructions,
                reason=reason,
                will_retry=will_retry,
            )
            self._session_manager.append_compaction(
                summary=result.summary,
                first_kept_entry_id=result.first_kept_entry_id,
                tokens_before=result.tokens_before,
            )
            # Only announce manual /compact — automatic compaction already
            # surfaces through the spinner's "Compacting…" phase and the
            # summary entry, so a notification would just be noise.
            if manual and self._runtime is not None:
                from tau.extensions.context import ExtensionContext

                ctx = ExtensionContext.from_runtime(self._runtime)
                if ctx.ui is not None:
                    ctx.ui.notify("Compaction completed.")
            self._compaction_failures = 0
            self._compaction_circuit_notified = False
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
        except _CompactionCancelledError:
            raise
        except Exception as error:
            await self.hooks.emit(
                CompactionFailureEvent(
                    manual=manual,
                    reason=reason,
                    will_retry=will_retry,
                    error=str(error),
                )
            )
            raise
        finally:
            self._phase = previous_phase

    def _latest_model_change_timestamp(self) -> float | None:
        """Timestamp of the most recent model-change entry in the active branch, if any."""
        from tau.session.types import ModelChangeEntry

        for entry in reversed(self._session_manager.get_branch()):
            if isinstance(entry, ModelChangeEntry):
                return entry.timestamp
        return None

    def _current_compaction_settings(self) -> CompactionSettings:
        """Resolve live settings and clamp them to the active model window."""
        from tau.session.compaction import validated_compaction_settings

        settings_manager = self._engine._settings
        if settings_manager is None:
            settings = self._config.compaction
        else:
            settings = CompactionSettings(
                enabled=settings_manager.is_compaction_enabled(),
                reserve_tokens=settings_manager.get_compaction_reserve_tokens(),
                keep_recent_tokens=settings_manager.get_compaction_keep_recent_tokens(),
            )
        return validated_compaction_settings(settings, self._context_window)

    def _record_compaction_failure(self, message: str) -> None:
        """Increment the circuit breaker and notify once when it opens."""
        self._compaction_failures += 1
        _log.exception(message)
        if self._compaction_failures >= 3 and not self._compaction_circuit_notified:
            self._compaction_circuit_notified = True
            self._notify(
                "Automatic compaction disabled after 3 failures. "
                "Use /compact to retry manually or inspect the logs."
            )

    async def _check_compaction(self) -> bool:
        """Auto-compact if context usage exceeds the threshold. Circuit-breaks after 3 failures.

        Returns True when compaction ran and False otherwise.
        """
        from tau.session.compaction import (
            estimate_context_tokens,
            is_silent_overflow,
            latest_compaction_timestamp,
            prepare_compaction,
            should_compact,
        )

        if self._abort_requested:
            return False

        if self._compaction_failures >= 3:
            return False

        settings = self._current_compaction_settings()
        if not settings.enabled:
            return False

        entries = self._session_manager.get_branch()
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)

        # "Silent" overflow: some providers accept an over-limit prompt and return a
        # successful response (z.ai) or truncate the input and stop with no output
        # (Xiaomi MiMo) instead of erroring. The threshold check can miss these, so
        # force compaction when the last response shows the symptom.
        last = self._session_manager.find_last_assistant_message()

        # Model-switch guard: if the last assistant message is older than the most recent
        # model change, it came from a different model. Treating its usage/overflow data as
        # a signal for the new model is unreliable (context windows differ), so skip.
        model_change_ts = self._latest_model_change_timestamp()
        usage_is_stale = (
            model_change_ts is not None and last is not None and last.timestamp <= model_change_ts
        )
        usage = estimate_context_tokens(
            llm_messages,
            system_prompt=self._system_prompt,
            tools=self._engine.tools,
            ignore_usage=usage_is_stale,
        )

        forced = (
            not usage_is_stale
            and last is not None
            and is_silent_overflow(last, self._context_window)
        )

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
                preparation,
                entries,
                manual=False,
                reason=_CompactionReason.Overflow if forced else _CompactionReason.Threshold,
            )
            return True
        except _CompactionCancelledError:
            return False
        except Exception:
            self._record_compaction_failure("Auto-compaction failed")
            return False

    def _estimate_indicates_overflow(self) -> bool:
        """Numeric fallback for overflow detection, independent of error text.

        A failed request never gets a provider-reported usage back, so there's
        no "provider said input tokens exceeded the window" signal to check —
        only Tau's own pre-send estimate for what was just sent. Some providers
        reject an over-window request with phrasing that matches none of
        _CONTEXT_OVERFLOW_PATTERNS (tau/inference/utils.py) — NVIDIA's
        "max_tokens must be at least 1, got -128" was one instance, where the
        gateway computed context_window - prompt_tokens server-side and
        rejected the negative result. If Tau's own estimate for the request
        that just failed already reached the model's context window, treat it
        as overflow regardless of how the provider phrased the rejection.
        """
        from tau.session.compaction import estimate_context_tokens

        if self._context_window <= 0:
            return False
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        usage = estimate_context_tokens(
            llm_messages, system_prompt=self._system_prompt, tools=self._engine.tools
        )
        return usage.tokens >= self._context_window

    async def _try_overflow_recovery(self) -> bool:
        """If the last turn died with a context-overflow error, compact once and signal a retry.

        Drops the error message so it isn't kept or used as a stale anchor, compacts the
        history, and lets the caller re-run the turn. Bounded to one attempt per turn so a
        session that overflows even after compaction fails cleanly.
        """
        from tau.inference.utils import ErrorKind
        from tau.session.compaction import prepare_compaction

        settings = self._current_compaction_settings()
        if not settings.enabled:
            return False

        last = self._session_manager.find_last_assistant_message()
        if last is None:
            return False
        if (
            last.error_kind != ErrorKind.CONTEXT_OVERFLOW
            and not self._estimate_indicates_overflow()
        ):
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
        preparation = prepare_compaction(entries, settings)
        if preparation is None:
            return False
        try:
            await self._apply_compaction(
                preparation, entries, manual=False, reason=_CompactionReason.Overflow
            )
        except _CompactionCancelledError:
            return False
        except Exception:
            self._record_compaction_failure("Overflow-triggered compaction failed")
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
            CompactionCancelledEvent,
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

        provided = None
        for res in before_results:
            if not isinstance(res, BeforeCompactionResult):
                continue
            if res.cancel:
                await self.hooks.emit(
                    CompactionCancelledEvent(
                        manual=manual,
                        reason=reason,
                        will_retry=will_retry,
                    )
                )
                raise _CompactionCancelledError("Compaction cancelled by extension")
            if res.compaction is not None:
                provided = res.compaction
                break

        await self.hooks.emit(
            CompactionStartEvent(manual=manual, reason=reason, will_retry=will_retry)
        )
        if provided is not None:
            return provided, True
        result = await _compact(
            preparation, self._engine.llm, custom_instructions=custom_instructions
        )  # type: ignore[arg-type]
        return result, False

    # -------------------------------------------------------------------------
    # Core turn entry point
    # -------------------------------------------------------------------------

    def _replace_signal(self) -> None:
        """Install a fresh abort signal for the next engine run/continuation.

        Carries a pending abort into the new event so an abort issued while no
        run was in flight (compaction, save-point hooks) is not lost.
        """
        self._signal = asyncio.Event()
        if self._abort_requested:
            self._signal.set()
        self._engine.llm.api.options.signal = self._signal

    async def invoke(self, text: str, options: PromptOptions | None = None) -> None:
        """Run one user turn."""
        if self._phase != AgentPhase.IDLE:
            raise RuntimeError(
                f"Agent is busy (phase={self._phase!r}). Wait for the current operation to finish."
            )

        self._idle_event.clear()
        opts = options or PromptOptions()

        user_message = UserMessage.with_media(
            text,
            list(opts.images) if opts.images else None,
            list(opts.audio) if opts.audio else None,
            list(opts.video) if opts.video else None,
            list(opts.file) if opts.file else None,
        )
        # See _on_message_end: append_message() does a full session-file
        # merge-and-rewrite under a FileLock now, not a cheap append.
        await asyncio.to_thread(self._session_manager.append_message, user_message, meta=opts.meta)

        self._overflow_recovery_attempted = False
        self._abort_requested = False
        try:
            self._phase = AgentPhase.TURN
            try:
                while True:
                    ctx = self._build_turn_context()
                    self._replace_signal()
                    try:
                        await self._run(ctx)
                        break
                    except RuntimeError:
                        # On a context-overflow error, compact and retry the turn once.
                        if await self._try_overflow_recovery():
                            continue
                        raise

                while True:
                    # Messages may arrive after the engine's last queue poll, or
                    # from save-point/compaction handlers. Keep processing until
                    # the complete post-run lifecycle leaves both queues empty.
                    # An abort issued at any point (even between signal swaps)
                    # stops the lifecycle instead of running queued follow-ups.
                    while not self._abort_requested and self._engine.has_pending_messages():
                        self._replace_signal()
                        await self._run_continue()

                    if self._abort_requested:
                        break
                    await self.hooks.emit(SavePointEvent())
                    await self._check_compaction()
                    if not self._engine.has_pending_messages():
                        break
            except Exception:
                # A failed turn still ends the lifecycle, so settle before the
                # error leaves this frame. Without it, everything keyed on
                # `settled` — deferred /command and !terminal input waiting to
                # replay, RPC clients, footer badges — is left waiting on an
                # event that never arrives for the rest of the session.
                # Cancellation is deliberately not covered: CancelledError is a
                # BaseException, and awaiting a hook while unwinding a cancelled
                # task is not reliable.
                await self._settle()
                raise
            else:
                await self._settle()
            finally:
                self._phase = AgentPhase.IDLE
        finally:
            self._idle_event.set()

    async def _settle(self) -> None:
        """Mark the invocation lifecycle idle and announce it.

        Phase goes IDLE *before* the event is emitted: handlers gate on
        ``Agent.is_idle()`` (which reads the phase) to decide whether it is
        safe to start new work, so emitting first would make every one of
        them see a still-busy agent and skip.
        """
        self._phase = AgentPhase.IDLE
        await self.hooks.emit(SettledEvent())

    def _build_turn_context(self) -> EngineContext:
        """Build the LLM context for a turn from the current (possibly compacted) session."""
        session_ctx = self._session_manager.build_session_context()
        llm_messages = _to_llm_messages(session_ctx.messages)
        llm_messages = strip_unusable_trailing_assistant(llm_messages, self._session_manager)
        return EngineContext(
            system_prompt=self._system_prompt,
            messages=llm_messages,
            tools=self._engine.tools,
        )

    @contextlib.contextmanager
    def _session_sync(self):
        """Persist and render engine messages for the duration of a turn.

        Both turn entry points need the same wiring, and both must unsubscribe
        even when the turn raises — otherwise a failed turn leaves handlers
        attached and the next one double-persists every message.
        """
        unsubscribe = self.hooks.register(
            "message_end",
            lambda event: self._on_message_end(event),
        )
        unsubscribe_rollback = self.hooks.register(
            "message_rollback",
            lambda event: self._on_message_rollback(event),
        )
        try:
            yield
        finally:
            unsubscribe()
            unsubscribe_rollback()

    def _raise_if_engine_failed(self) -> None:
        """Surface an engine-recorded error as an exception to the caller."""
        error = self._engine.state.error_message
        if error is not None:
            raise RuntimeError(f"Agent failed: {error}.")

    async def _run(self, ctx: EngineContext) -> None:
        with self._session_sync():
            await self._engine.run(ctx, signal=self._signal)
        self._raise_if_engine_failed()

    async def _run_continue(self) -> None:
        """Run a continuation turn that drains queued steering/follow-up messages.

        Mirrors ``_run``'s session-sync wiring so messages injected by the engine
        continuation are persisted and rendered just like a normal turn's.

        Keeps the continuation within the context window the same way the main turn
        does: auto-compact if needed, then resync the engine's history from the
        (possibly compacted) session — ``run_continue`` runs from ``state.messages``,
        which compaction (which only rewrites the session) would otherwise not touch.
        """
        await self._check_compaction()
        session_ctx = self._session_manager.build_session_context()
        self._engine.state.messages = _to_llm_messages(session_ctx.messages)

        with self._session_sync():
            await self._engine.run_continue(signal=self._signal)
        self._raise_if_engine_failed()
