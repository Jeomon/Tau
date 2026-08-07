"""One place that answers "what is in this session, and what did it cost".

The `/session` panel and RPC's ``get_session_stats`` used to compute this
separately, and diverged: the panel counted tool calls, tokens and spend, while
RPC returned message counts only, so a client had to pull every entry and
re-derive the rest — including the cache-accounting rule below, which it had no
way to know about.

Compaction and branch-summary entries are counted too. Both are real model
calls, and unlike a message nothing else in the history carries what they cost,
so leaving them out under-reports every session that has compacted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tau.message.types import Usage
from tau.message.utils import add_usage


@dataclass
class SessionStats:
    """Counts and spend for one branch of a session."""

    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    #: Compaction and branch-summary entries on this branch.
    summaries: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def total_messages(self) -> int:
        """User plus assistant messages, plus one for the tool-result block.

        Tool results are folded into a single logical message the way the
        transcript renders them, not counted per result.
        """
        return self.user_messages + self.assistant_messages + (1 if self.tool_results else 0)

    @property
    def total_tokens(self) -> int:
        return (
            self.usage.input_tokens
            + self.usage.output_tokens
            + self.usage.cache_read_tokens
            + self.usage.cache_write_tokens
        )

    @property
    def total_cost(self) -> float:
        return self.usage.cost.total

    def to_dict(self) -> dict[str, Any]:
        """The wire shape for RPC's ``get_session_stats``."""
        return {
            "userMessages": self.user_messages,
            "assistantMessages": self.assistant_messages,
            "toolCalls": self.tool_calls,
            "toolResults": self.tool_results,
            "summaries": self.summaries,
            "totalMessages": self.total_messages,
            "usage": {
                "inputTokens": self.usage.input_tokens,
                "outputTokens": self.usage.output_tokens,
                "cacheReadTokens": self.usage.cache_read_tokens,
                "cacheWriteTokens": self.usage.cache_write_tokens,
                "totalTokens": self.total_tokens,
                "cost": {
                    "input": self.usage.cost.input,
                    "output": self.usage.cost.output,
                    "cacheRead": self.usage.cost.cache_read,
                    "cacheWrite": self.usage.cost.cache_write,
                    "total": self.usage.cost.total,
                },
            },
        }


def compute_session_stats(entries: list[Any]) -> SessionStats:
    """Walk one branch and total up what it contains and what it cost."""
    from tau.message.types import AssistantMessage, ToolMessage, UserMessage
    from tau.session.types import BranchSummaryEntry, CompactionEntry, MessageEntry

    stats = SessionStats()

    for entry in entries:
        if isinstance(entry, (CompactionEntry, BranchSummaryEntry)):
            stats.summaries += 1
            add_usage(stats.usage, entry.usage)
            continue
        if not isinstance(entry, MessageEntry):
            continue
        msg = entry.message
        if isinstance(msg, UserMessage):
            stats.user_messages += 1
        elif isinstance(msg, AssistantMessage):
            stats.assistant_messages += 1
            stats.tool_calls += len(msg.tool_calls())
            add_usage(stats.usage, msg.usage)
        elif isinstance(msg, ToolMessage):
            stats.tool_results += len(msg.contents)

    return stats
