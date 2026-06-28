"""
Branch summarization for tree navigation.

When navigating to a different point in the session tree, this generates
a summary of the branch being left so context isn't lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM
    from tau.session.manager import SessionManager
    from tau.session.types import SessionEntry


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class BranchSummaryResult:
    summary: str | None = None
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    aborted: bool = False
    error: str | None = None


@dataclass
class BranchSummaryDetails:
    """Stored in BranchSummaryEntry.details for cumulative file tracking."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


@dataclass
class FileOperations:
    read: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


@dataclass
class BranchPreparation:
    messages: list
    file_ops: FileOperations
    total_tokens: int


@dataclass
class CollectEntriesResult:
    entries: list[SessionEntry]
    common_ancestor_id: str | None


# ---------------------------------------------------------------------------
# Entry collection
# ---------------------------------------------------------------------------


def collect_entries_for_branch_summary(
    session: SessionManager,
    old_leaf_id: str | None,
    target_id: str,
) -> CollectEntriesResult:
    """Collect entries that should be summarized when navigating from old_leaf_id to target_id."""
    if not old_leaf_id:
        return CollectEntriesResult(entries=[], common_ancestor_id=None)

    old_path = {e.id for e in session.get_branch(old_leaf_id)}
    target_path = session.get_branch(target_id)

    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry.id in old_path:
            common_ancestor_id = entry.id
            break

    entries: list[SessionEntry] = []
    current: str | None = old_leaf_id
    while current and current != common_ancestor_id:
        entry = session.get_entry(current)
        if not entry:
            break
        entries.append(entry)
        current = entry.parent_id

    entries.reverse()
    return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)


# ---------------------------------------------------------------------------
# Entry → message conversion
# ---------------------------------------------------------------------------


def _get_message_from_entry(entry: Any) -> Any | None:
    from tau.message.types import BranchSummaryMessage, CompactionSummaryMessage, CustomMessage
    from tau.session.types import (
        BranchSummaryEntry,
        CompactionEntry,
        CustomMessageEntry,
        MessageEntry,
    )

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
    if isinstance(entry, CompactionEntry):
        return CompactionSummaryMessage(
            summary=entry.summary,
            tokens_before=entry.tokens_before,
            timestamp=entry.timestamp,
        )
    return None


def _collect_file_ops(entries: list[Any], file_ops: FileOperations) -> None:
    """Collect successful file operations across all entries."""
    from tau.message.types import (
        AssistantMessage,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
    )

    calls: dict[str, tuple[str, str]] = {}
    for entry in entries:
        message = _get_message_from_entry(entry)
        if isinstance(message, AssistantMessage):
            for content in message.contents:
                if not isinstance(content, ToolCallContent):
                    continue
                path = content.args.get("path") if isinstance(content.args, dict) else None
                if isinstance(path, str) and content.name in {"read", "write", "edit"}:
                    calls[content.id] = (content.name, path)
        elif isinstance(message, ToolMessage):
            for content in message.contents:
                if not isinstance(content, ToolResultContent) or content.is_error:
                    continue
                operation = calls.get(content.id)
                if operation is None:
                    continue
                name, path = operation
                if name == "read":
                    file_ops.read.add(path)
                elif name == "write":
                    file_ops.written.add(path)
                elif name == "edit":
                    file_ops.edited.add(path)


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def prepare_branch_entries(entries: list[Any], token_budget: int = 0) -> BranchPreparation:
    """
    Walk entries from newest to oldest, adding messages until the token budget is hit.
    File ops are collected from ALL entries regardless of token budget.
    """
    from tau.session.compaction import estimate_tokens
    from tau.session.types import BranchSummaryEntry

    messages: list = []
    file_ops = FileOperations()
    total_tokens = 0

    # First pass: collect file ops from existing branch summaries
    for entry in entries:
        if isinstance(entry, BranchSummaryEntry) and not entry.from_hook and entry.details:
            details = entry.details
            if isinstance(details, dict):
                for f in details.get("read_files", []):
                    file_ops.read.add(f)
                for f in details.get("modified_files", []):
                    file_ops.edited.add(f)

    _collect_file_ops(entries, file_ops)

    # Second pass: walk newest→oldest, collect messages within budget
    for entry in reversed(entries):
        message = _get_message_from_entry(entry)
        if not message:
            continue

        tokens = estimate_tokens(message)

        if token_budget > 0 and total_tokens + tokens > token_budget:
            from tau.session.types import CompactionEntry

            if (
                isinstance(entry, (CompactionEntry, BranchSummaryEntry))
                and total_tokens < token_budget * 0.9
            ):
                messages.insert(0, message)
                total_tokens += tokens
            elif not messages:
                # Keep the newest oversized message; prompt construction applies
                # a hard model-aware bound before invoking the provider.
                messages.insert(0, message)
                total_tokens += tokens
            break

        messages.insert(0, message)
        total_tokens += tokens

    return BranchPreparation(messages=messages, file_ops=file_ops, total_tokens=total_tokens)


# ---------------------------------------------------------------------------
# File list formatting
# ---------------------------------------------------------------------------


def _compute_file_lists(file_ops: FileOperations) -> tuple[list[str], list[str]]:
    modified = file_ops.edited | file_ops.written
    read_only = sorted(f for f in file_ops.read if f not in modified)
    modified_files = sorted(modified)
    return read_only, modified_files


def _format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    sections: list[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

BRANCH_SUMMARY_PREAMBLE = (
    "The user explored a different conversation branch before returning here.\n"
    "Summary of that exploration:\n\n"
)

BRANCH_SUMMARY_PROMPT = """\
Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


async def generate_branch_summary(
    entries: list[Any],
    llm: TextLLM,
    context_window: int = 128_000,
    reserve_tokens: int = 16_384,
    custom_instructions: str | None = None,
    replace_instructions: bool = False,
) -> BranchSummaryResult:
    """Generate a summary of abandoned branch entries."""
    import asyncio

    from tau.inference.types import LLMContext, TextDeltaEvent, TextEndEvent
    from tau.message.types import UserMessage
    from tau.session.compaction import (
        SUMMARIZATION_SYSTEM_PROMPT,
        _truncate_middle,
        serialize_conversation,
    )

    if replace_instructions and custom_instructions:
        instructions = custom_instructions
    elif custom_instructions:
        instructions = f"{BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {custom_instructions}"
    else:
        instructions = BRANCH_SUMMARY_PROMPT

    context_window = max(1, context_window)
    prompt_overhead_tokens = (
        len(SUMMARIZATION_SYSTEM_PROMPT) + len(instructions) + len("<conversation></conversation>")
    ) // 4
    summary_margin = min(2_048, max(1, context_window // 4))
    reserve_tokens = min(max(1, reserve_tokens), max(1, context_window - summary_margin))
    token_budget = max(1, context_window - reserve_tokens - prompt_overhead_tokens)
    prep = prepare_branch_entries(entries, token_budget)

    if not prep.messages:
        return BranchSummaryResult(summary="No content to summarize")

    conversation_text = serialize_conversation(prep.messages)
    prefix = "<conversation>\n"
    suffix = f"\n</conversation>\n\n{instructions}"
    prompt_chars = max(1, context_window - reserve_tokens) * 4
    conversation_budget = max(1, prompt_chars - len(prefix) - len(suffix))
    conversation_text = _truncate_middle(conversation_text, conversation_budget)
    prompt_text = _truncate_middle(f"{prefix}{conversation_text}{suffix}", prompt_chars)

    context = LLMContext(
        messages=[UserMessage.from_text(prompt_text)],
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
    )
    try:
        events = await llm.invoke(context)
    except asyncio.CancelledError:
        return BranchSummaryResult(aborted=True)
    except Exception as error:
        return BranchSummaryResult(error=str(error))

    text_end = next((e for e in events if isinstance(e, TextEndEvent)), None)
    if text_end:
        raw = text_end.text.content
    else:
        raw = "".join(e.text.content for e in events if isinstance(e, TextDeltaEvent))

    if not raw.strip():
        return BranchSummaryResult(error="Provider returned an empty branch summary.")

    summary = BRANCH_SUMMARY_PREAMBLE + raw

    read_files, modified_files = _compute_file_lists(prep.file_ops)
    summary += _format_file_operations(read_files, modified_files)

    return BranchSummaryResult(
        summary=summary or "No summary generated",
        read_files=read_files,
        modified_files=modified_files,
    )
