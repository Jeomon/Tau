"""
Context compaction for long agent sessions.

Compaction algorithm:
- Backwards-walk cut-point detection (never cuts mid-tool-result)
- Turn-split handling with a separate prefix summary
- Iterative summary merging (update prompt on subsequent compactions)
- File operation tracking appended to summary
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


class ThresholdCompactionStop(Exception):
    """Raised by transform_context after threshold compaction to stop the engine loop.

    Threshold compaction means the context hit the configured limit mid-turn.
    The turn stops cleanly so the user can review the compacted context and
    continue manually willRetry=false behaviour for threshold
    compaction.
    """


if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass
class CompactionSettings:
    """Configuration for context compaction behavior."""

    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()

TOOL_RESULT_MAX_CHARS = 2_000
ESTIMATED_IMAGE_CHARS = 4_800
SUMMARY_SAFETY_TOKENS = 2_048


# ---------------------------------------------------------------------------
# Result / preparation types
# ---------------------------------------------------------------------------


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: dict[str, Any] | None = None


@dataclass
class CompactionPreparation:
    """Preparation data for a compaction operation."""

    first_kept_entry_id: str
    messages_to_summarize: list
    turn_prefix_messages: list
    is_split_turn: bool
    tokens_before: int
    settings: CompactionSettings
    previous_summary: str | None = None


@dataclass
class ContextUsageEstimate:
    """Estimate of context token usage."""

    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None


@dataclass
class CutPointResult:
    """Result of finding a context cut point for compaction."""

    first_kept_entry_index: int
    is_split_turn: bool = False
    turn_start_index: int = -1


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(message: Any) -> int:
    """Estimate token count for any AgentMessage using chars/4 heuristic."""
    from tau.message.types import (
        AssistantMessage,
        BranchSummaryMessage,
        CompactionSummaryMessage,
        CustomMessage,
        ImageContent,
        TerminalExecutionMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )

    chars = 0
    if isinstance(message, UserMessage):
        for c in message.contents:
            if isinstance(c, TextContent):
                chars += len(c.content)
            elif isinstance(c, ImageContent):
                chars += ESTIMATED_IMAGE_CHARS
    elif isinstance(message, AssistantMessage):
        for c in message.contents:
            if isinstance(c, (TextContent, ThinkingContent)):
                chars += len(c.content)
            elif isinstance(c, ToolCallContent):
                chars += len(c.name) + len(json.dumps(c.args))
    elif isinstance(message, ToolMessage):
        for c in message.contents:
            if isinstance(c, ToolResultContent):
                chars += len(c.content)
    elif isinstance(message, TerminalExecutionMessage):
        chars = len(message.command) + len(message.output)
    elif isinstance(message, (CompactionSummaryMessage, BranchSummaryMessage)):
        chars = len(message.summary)
    elif isinstance(message, CustomMessage):
        for c in message.contents:
            if isinstance(c, TextContent):
                chars += len(c.content)

    return max(1, chars // 4)


def effective_usage_tokens(usage: Any) -> int:
    """Return context tokens without double-counting provider cache metrics."""
    cache_read = 0
    if not getattr(usage, "input_tokens_include_cache_read", False):
        cache_read = getattr(usage, "cache_read_tokens", 0) or 0
    return (
        (getattr(usage, "input_tokens", 0) or 0)
        + (getattr(usage, "output_tokens", 0) or 0)
        + cache_read
        + (getattr(usage, "cache_write_tokens", 0) or 0)
    )


def _request_overhead_tokens(system_prompt: str, tools: list[Any]) -> int:
    chars = len(system_prompt)
    for tool in tools:
        chars += len(getattr(tool, "name", ""))
        chars += len(getattr(tool, "description", ""))
        schema = getattr(tool, "schema", None)
        if schema is not None:
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                chars += len(json.dumps(schema.model_json_schema()))
    return chars // 4


def estimate_context_tokens(
    messages: list,
    *,
    system_prompt: str = "",
    tools: list[Any] | None = None,
    ignore_usage: bool = False,
) -> ContextUsageEstimate:
    """
    Estimate total context tokens.

    Uses the Usage object from the last non-aborted/non-error assistant message
    as a precise anchor, then estimates trailing messages with chars/4.
    """
    from tau.inference.types import StopReason
    from tau.message.types import AssistantMessage

    last_usage: int | None = None
    last_usage_idx: int | None = None

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AssistantMessage) and msg.stop_reason not in (
            StopReason.Abort,
            StopReason.Error,
        ):
            u = msg.usage
            total = 0 if ignore_usage else effective_usage_tokens(u)
            if total > 0:
                last_usage = total
                last_usage_idx = i
                break

    if last_usage is None:
        estimated = sum(estimate_tokens(m) for m in messages)
        estimated += _request_overhead_tokens(system_prompt, tools or [])
        return ContextUsageEstimate(
            tokens=estimated,
            usage_tokens=0,
            trailing_tokens=estimated,
            last_usage_index=None,
        )

    trailing = sum(estimate_tokens(messages[i]) for i in range(last_usage_idx + 1, len(messages)))  # type: ignore[operator]
    return ContextUsageEstimate(
        tokens=last_usage + trailing,
        usage_tokens=last_usage,
        trailing_tokens=trailing,
        last_usage_index=last_usage_idx,
    )


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    if not settings.enabled or context_window <= 0:
        return False
    return context_tokens >= context_window - settings.reserve_tokens


def validated_compaction_settings(
    settings: CompactionSettings,
    context_window: int,
) -> CompactionSettings:
    """Clamp compaction budgets so compacted context fits the active model."""
    if context_window <= 0:
        return CompactionSettings(
            enabled=settings.enabled,
            reserve_tokens=max(1, settings.reserve_tokens),
            keep_recent_tokens=max(1, settings.keep_recent_tokens),
        )
    summary_margin = min(
        SUMMARY_SAFETY_TOKENS,
        max(1, context_window // 4),
    )
    reserve = min(
        max(1, settings.reserve_tokens),
        max(1, context_window - summary_margin),
    )
    max_recent = max(1, context_window - reserve - summary_margin)
    return CompactionSettings(
        enabled=settings.enabled,
        reserve_tokens=reserve,
        keep_recent_tokens=min(max(1, settings.keep_recent_tokens), max_recent),
    )


# ---------------------------------------------------------------------------
# Cut-point detection
# ---------------------------------------------------------------------------


def _is_valid_cut_point(entry: Any) -> bool:
    """Return True if this session entry is a valid place to cut history."""
    from tau.inference.types import StopReason
    from tau.message.types import (
        AssistantMessage,
        CustomMessage,
        TerminalExecutionMessage,
        UserMessage,
    )
    from tau.session.types import BranchSummaryEntry, CustomMessageEntry, MessageEntry

    if isinstance(entry, (CustomMessageEntry, BranchSummaryEntry)):
        return True
    if isinstance(entry, MessageEntry):
        msg = entry.message
        if isinstance(msg, (UserMessage, TerminalExecutionMessage, CustomMessage)):
            return True
        if isinstance(msg, AssistantMessage):
            # Skip aborted-empty assistants — they are visual markers only
            return not (msg.stop_reason == StopReason.Abort and not msg.contents)
    return False


def _entry_message(entry: Any) -> Any | None:
    """Extract AgentMessage from a session entry, skipping compaction entries."""
    from tau.message.types import BranchSummaryMessage, CustomMessage
    from tau.session.types import (
        BranchSummaryEntry,
        CompactionEntry,
        CustomMessageEntry,
        MessageEntry,
    )

    if isinstance(entry, CompactionEntry):
        return None
    if isinstance(entry, MessageEntry):
        return entry.message
    if isinstance(entry, CustomMessageEntry):
        return CustomMessage.from_session(entry=entry)
    if isinstance(entry, BranchSummaryEntry):
        return BranchSummaryMessage(
            summary=entry.summary,
            from_id=entry.from_id,
            timestamp=entry.timestamp,
        )
    return None


def find_cut_point(
    entries: list,
    start_idx: int,
    end_idx: int,
    keep_recent_tokens: int,
) -> CutPointResult:
    """
    Walk backwards from newest, accumulating estimated token sizes.
    Stop when the budget is met. Return the first entry to keep.

    Valid cut points: user, assistant, terminal, custom messages.
    Never cuts at tool messages (must follow their assistant).
    """
    from tau.message.types import TerminalExecutionMessage, UserMessage
    from tau.session.types import CompactionEntry, MessageEntry

    # Collect valid cut points in [start_idx, end_idx)
    cut_points: list[int] = []
    for i in range(start_idx, end_idx):
        if _is_valid_cut_point(entries[i]):
            cut_points.append(i)

    if not cut_points:
        return CutPointResult(first_kept_entry_index=start_idx)

    accumulated = 0
    cut_index = cut_points[0]  # default: keep everything from boundary start

    for i in range(end_idx - 1, start_idx - 1, -1):
        entry = entries[i]
        if not isinstance(entry, MessageEntry):
            continue
        accumulated += estimate_tokens(entry.message)
        if accumulated >= keep_recent_tokens:
            # Snap to the nearest valid cut point at or after i
            for cp in cut_points:
                if cp >= i:
                    cut_index = cp
                    break
            break

    # Walk backwards to include any adjacent non-message, non-compaction entries
    while cut_index > start_idx:
        prev = entries[cut_index - 1]
        if isinstance(prev, (MessageEntry, CompactionEntry)):
            break
        cut_index -= 1

    # Determine whether we're splitting a turn
    cut_entry = entries[cut_index]
    is_user_start = isinstance(cut_entry, MessageEntry) and isinstance(
        cut_entry.message, (UserMessage, TerminalExecutionMessage)
    )

    from tau.session.types import BranchSummaryEntry, CustomMessageEntry

    if is_user_start or isinstance(cut_entry, (CustomMessageEntry, BranchSummaryEntry)):
        return CutPointResult(first_kept_entry_index=cut_index)

    # Find the user message that started the turn containing cut_index
    turn_start = -1
    for i in range(cut_index, start_idx - 1, -1):
        e = entries[i]
        if isinstance(e, MessageEntry) and isinstance(
            e.message, (UserMessage, TerminalExecutionMessage)
        ):
            turn_start = i
            break
        if isinstance(e, (CustomMessageEntry, BranchSummaryEntry)):
            turn_start = i
            break

    return CutPointResult(
        first_kept_entry_index=cut_index,
        is_split_turn=(turn_start != -1),
        turn_start_index=turn_start,
    )


# ---------------------------------------------------------------------------
# Serialization (prevents the LLM from continuing the conversation)
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated} more characters truncated]"


def _truncate_middle(text: str, max_chars: int) -> str:
    """Fit text while retaining both the oldest and newest context."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n\n[... middle content omitted for summary budget ...]\n\n"
    available = max(0, max_chars - len(marker))
    head = available // 2
    tail = available - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}"


def serialize_conversation(messages: list) -> str:
    """
    Render AgentMessages as flat text for the summarisation LLM.
    Tool results are truncated to keep the prompt compact.
    """
    from tau.message.types import (
        AssistantMessage,
        BranchSummaryMessage,
        CompactionSummaryMessage,
        CustomMessage,
        TerminalExecutionMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )

    parts: list[str] = []

    for msg in messages:
        if isinstance(msg, CompactionSummaryMessage):
            parts.append(f"[Context Summary]:\n{msg.summary}")

        elif isinstance(msg, BranchSummaryMessage):
            parts.append(f"[Branch Summary]:\n{msg.summary}")

        elif isinstance(msg, UserMessage):
            text = "".join(c.content for c in msg.contents if isinstance(c, TextContent))
            if text:
                parts.append(f"[User]: {text}")

        elif isinstance(msg, AssistantMessage):
            thinking_parts = [c.content for c in msg.contents if isinstance(c, ThinkingContent)]
            text_parts = [c.content for c in msg.contents if isinstance(c, TextContent)]
            tool_calls = [
                f"{c.name}({', '.join(f'{k}={json.dumps(v)}' for k, v in c.args.items())})"
                for c in msg.contents
                if isinstance(c, ToolCallContent)
            ]
            if thinking_parts:
                parts.append(f"[Assistant thinking]: {' '.join(thinking_parts)}")
            if text_parts:
                parts.append(f"[Assistant]: {' '.join(text_parts)}")
            if tool_calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")

        elif isinstance(msg, ToolMessage):
            for c in msg.contents:
                if isinstance(c, ToolResultContent) and c.content:
                    parts.append(f"[Tool result]: {_truncate(c.content, TOOL_RESULT_MAX_CHARS)}")

        elif isinstance(msg, TerminalExecutionMessage):
            text = f"Ran `{msg.command}`"
            if msg.output:
                text += f"\n```\n{_truncate(msg.output, TOOL_RESULT_MAX_CHARS)}\n```"
            parts.append(f"[Terminal]: {text}")

        elif isinstance(msg, CustomMessage):
            text = "".join(c.content for c in msg.contents if isinstance(c, TextContent))
            if text:
                parts.append(f"[{msg.custom_type}]: {text}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Summarisation prompts
# ---------------------------------------------------------------------------

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. "
    "Read the conversation and produce a structured summary following the exact format requested. "
    "Do NOT continue the conversation. Do NOT respond to questions in it. Output ONLY the summary."
)

SUMMARIZATION_PROMPT = """\
The messages above are a conversation to summarize. Create a structured context checkpoint \
summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish?]

## Constraints & Preferences
- [Any constraints or preferences the user mentioned, or "(none)"]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Data, examples, or references needed to continue, or "(none)"]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """\
The messages above are NEW conversation messages to incorporate into the existing summary \
provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Previously done items AND newly completed items]

### In Progress
- [ ] [Current work — updated based on progress]

### Blocked
- [Current blockers — remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

TURN_PREFIX_SUMMARIZATION_PROMPT = """\
This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix."""


# ---------------------------------------------------------------------------
# Summary generation (LLM call)
# ---------------------------------------------------------------------------


async def _call_llm_for_summary(
    prompt_text: str,
    llm: TextLLM,
) -> str:
    from tau.inference.types import LLMContext, TextDeltaEvent, TextEndEvent
    from tau.message.types import UserMessage

    context = LLMContext(
        messages=[UserMessage.from_text(prompt_text)],
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
    )
    events = await llm.invoke(context)

    # Prefer TextEndEvent (full accumulated text); fall back to concatenating deltas
    text_end = next((e for e in events if isinstance(e, TextEndEvent)), None)
    if text_end:
        return text_end.text.content

    return "".join(e.text.content for e in events if isinstance(e, TextDeltaEvent))


async def generate_summary(
    messages: list,
    llm: TextLLM,
    reserve_tokens: int,
    previous_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    conversation_text = serialize_conversation(messages)

    base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    suffix = "</conversation>\n\n"
    if previous_summary:
        suffix += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    suffix += base_prompt

    input_limit = getattr(getattr(llm, "model", None), "input_limit", 0) or 0
    prompt_chars = 0
    if input_limit > 0:
        prompt_chars = max(1, input_limit - reserve_tokens) * 4
        conversation_budget = max(1, prompt_chars - len("<conversation>\n") - len(suffix))
        conversation_text = _truncate_middle(conversation_text, conversation_budget)
    prompt = f"<conversation>\n{conversation_text}\n{suffix}"
    if input_limit > 0:
        prompt = _truncate_middle(prompt, prompt_chars)

    return await _call_llm_for_summary(prompt, llm)


async def _generate_turn_prefix_summary(
    messages: list,
    llm: TextLLM,
    reserve_tokens: int,
) -> str:
    conversation_text = serialize_conversation(messages)
    suffix = f"</conversation>\n\n{TURN_PREFIX_SUMMARIZATION_PROMPT}"
    input_limit = getattr(getattr(llm, "model", None), "input_limit", 0) or 0
    prompt_chars = 0
    if input_limit > 0:
        prompt_chars = max(1, input_limit - reserve_tokens) * 4
        conversation_budget = max(1, prompt_chars - len("<conversation>\n") - len(suffix))
        conversation_text = _truncate_middle(conversation_text, conversation_budget)
    prompt = f"<conversation>\n{conversation_text}\n{suffix}"
    if input_limit > 0:
        prompt = _truncate_middle(prompt, prompt_chars)
    return await _call_llm_for_summary(prompt, llm)


# ---------------------------------------------------------------------------
# Prepare — pure, no I/O
# ---------------------------------------------------------------------------


def prepare_compaction(
    entries: list,
    settings: CompactionSettings,
) -> CompactionPreparation | None:
    from tau.session.types import CompactionEntry

    if not entries:
        return None

    # Don't compact if the last entry is already a compaction
    if isinstance(entries[-1], CompactionEntry):
        return None

    # Find the most recent previous compaction
    prev_compaction: CompactionEntry | None = None
    prev_compaction_idx = -1
    for i in range(len(entries) - 1, -1, -1):
        if isinstance(entries[i], CompactionEntry):
            prev_compaction = entries[i]
            prev_compaction_idx = i
            break

    previous_summary: str | None = None
    boundary_start = 0
    if prev_compaction is not None:
        previous_summary = prev_compaction.summary
        first_kept_idx = next(
            (i for i, e in enumerate(entries) if e.id == prev_compaction.first_kept_entry_id),
            prev_compaction_idx + 1,
        )
        boundary_start = first_kept_idx

    boundary_end = len(entries)

    # Estimate current context size from all messages in the branch
    all_messages = [m for e in entries if (m := _entry_message(e)) is not None]
    tokens_before = estimate_context_tokens(all_messages).tokens

    cut = find_cut_point(entries, boundary_start, boundary_end, settings.keep_recent_tokens)

    first_kept_entry = entries[cut.first_kept_entry_index]
    first_kept_entry_id: str = getattr(first_kept_entry, "id", "")
    if not first_kept_entry_id:
        return None

    # Nothing to cut — the entire conversation fits in the keep budget, so
    # there is no history to summarise.  Returning None skips compaction.
    if cut.first_kept_entry_index == boundary_start and not cut.is_split_turn:
        return None

    history_end = cut.turn_start_index if cut.is_split_turn else cut.first_kept_entry_index

    messages_to_summarize = [
        m
        for i in range(boundary_start, history_end)
        if (m := _entry_message(entries[i])) is not None
    ]

    turn_prefix_messages: list = []
    if cut.is_split_turn:
        turn_prefix_messages = [
            m
            for i in range(cut.turn_start_index, cut.first_kept_entry_index)
            if (m := _entry_message(entries[i])) is not None
        ]

    return CompactionPreparation(
        first_kept_entry_id=first_kept_entry_id,
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        is_split_turn=cut.is_split_turn,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Main compact() — async, calls LLM
# ---------------------------------------------------------------------------


async def compact(
    preparation: CompactionPreparation,
    llm: TextLLM,
    custom_instructions: str | None = None,
) -> CompactionResult:
    settings = preparation.settings

    if preparation.is_split_turn and preparation.turn_prefix_messages:
        # Generate both summaries in parallel
        async def _no_history() -> str:
            return "No prior history."

        history_coro = (
            generate_summary(
                preparation.messages_to_summarize,
                llm,
                settings.reserve_tokens,
                preparation.previous_summary,
                custom_instructions,
            )
            if preparation.messages_to_summarize
            else _no_history()
        )
        history_text, prefix_text = await asyncio.gather(
            history_coro,
            _generate_turn_prefix_summary(
                preparation.turn_prefix_messages,
                llm,
                settings.reserve_tokens,
            ),
        )
        summary = f"{history_text}\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_text}"
    else:
        summary = await generate_summary(
            preparation.messages_to_summarize,
            llm,
            settings.reserve_tokens,
            preparation.previous_summary,
            custom_instructions,
        )

    return CompactionResult(
        summary=summary,
        first_kept_entry_id=preparation.first_kept_entry_id,
        tokens_before=preparation.tokens_before,
    )


# ── Compaction trigger helpers ────────────────────────────────────────────────


def latest_compaction_timestamp(branch: list[Any]) -> float | None:
    """Return the timestamp of the most recent compaction entry in the branch, or None."""
    from tau.session.types import CompactionEntry

    for entry in reversed(branch):
        if isinstance(entry, CompactionEntry):
            return entry.timestamp
    return None


def is_silent_overflow(message: Any, context_window: int) -> bool:
    """Detect overflow on a *successful* response (no API error was raised).

    Two symptoms:
    - Silent (z.ai): stop_reason=Stop but input tokens exceed the window.
    - Length-stop (Xiaomi MiMo): stop_reason=Length with zero output tokens,
      meaning the server truncated the input to fill the window and had no room
      to generate anything.
    """
    from tau.inference.types import StopReason

    if context_window <= 0:
        return False
    u = message.usage
    inp = u.input_tokens
    if not u.input_tokens_include_cache_read:
        inp += u.cache_read_tokens
    if message.stop_reason == StopReason.Stop and inp > context_window:
        return True
    return bool(
        message.stop_reason == StopReason.Length
        and u.output_tokens == 0
        and inp >= context_window * 0.99
    )
