from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tau.message.types import ToolCallContent, ToolResultContent
    from tau.session.compaction import CompactionPreparation, CompactionResult
    from tau.session.types import SessionEntry
    from tau.tool.types import ToolResult


class AgentEndReason(StrEnum):
    """Why the agent loop finished."""

    Completed = "completed"
    Aborted = "aborted"
    Error = "error"


class CompactionReason(StrEnum):
    """What triggered a context compaction."""

    Manual = "manual"
    Threshold = "threshold"
    Overflow = "overflow"


@dataclass
class ContextEvent:
    """Carries the full message history just before it is sent to the LLM;
    handlers may rewrite it.
    """

    type: Literal["context"] = field(default="context", init=False)
    messages: list[Any] = field(default_factory=list)


@dataclass
class BeforeAgentStartEvent:
    """Fired after the user prompt is known but before the engine loop begins;
    handlers may override the system prompt.
    """

    type: Literal["before_agent_start"] = field(default="before_agent_start", init=False)
    prompt: str = ""
    system_prompt: str = ""


@dataclass
class AgentStartEvent:
    """Fired when the engine loop starts processing a new user prompt."""

    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass
class AgentEndEvent:
    """Fired when the engine loop finishes, carrying all messages produced and the exit reason."""

    type: Literal["agent_end"] = field(default="agent_end", init=False)
    messages: list[Any] = field(default_factory=list)
    reason: AgentEndReason = AgentEndReason.Completed


@dataclass
class AgentErrorEvent:
    """Fired when the engine loop terminates due to an unrecoverable error."""

    type: Literal["agent_error"] = field(default="agent_error", init=False)
    error: str = ""


@dataclass
class TurnStartEvent:
    """Fired at the beginning of each LLM inference turn within an agent loop."""

    type: Literal["turn_start"] = field(default="turn_start", init=False)
    turn_index: int = 0
    timestamp: float = 0.0


@dataclass
class TurnEndEvent:
    """Fired after a turn's assistant message and all tool results are available."""

    type: Literal["turn_end"] = field(default="turn_end", init=False)
    turn_index: int = 0
    message: Any = None
    tool_results: list[Any] = field(default_factory=list)


@dataclass
class MessageStartEvent:
    """Fired when the LLM begins streaming a new assistant message."""

    type: Literal["message_start"] = field(default="message_start", init=False)
    message: Any = None


@dataclass
class MessageUpdateEvent:
    """Fired on each incremental content chunk while the assistant message streams."""

    type: Literal["message_update"] = field(default="message_update", init=False)
    message: Any = None


@dataclass
class MessageEndEvent:
    """Fired when the assistant message is fully received;
    handlers may replace it via MessageEndEventResult.
    """

    type: Literal["message_end"] = field(default="message_end", init=False)
    message: Any = None


@dataclass
class MessageRollbackEvent:
    """Fired to drop the last ``count`` committed messages from history and UI.

    Used when an interrupted tool turn is discarded: the assistant tool-call
    message and its tool-result message were already persisted/rendered before
    the abort landed, so they must be retracted to keep history consistent.
    """

    type: Literal["message_rollback"] = field(default="message_rollback", init=False)
    count: int = 0


@dataclass
class ToolExecutionFailureEvent:
    """Fired when a tool raises an uncaught exception,
    distinct from a tool returning an error result.
    """

    type: Literal["tool_execution_failure"] = field(default="tool_execution_failure", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class ToolExecutionStartEvent:
    """Fired just before a tool's execute() is called."""

    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)
    tool_call: ToolCallContent


@dataclass
class ToolExecutionUpdateEvent:
    """Fired for each streaming progress update emitted by a long-running tool."""

    type: Literal["tool_execution_update"] = field(default="tool_execution_update", init=False)
    partial_tool_result: ToolResult


@dataclass
class ToolExecutionEndEvent:
    """Fired after a tool's execute() returns with the final ToolResultContent."""

    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)
    tool_result: ToolResultContent


@dataclass
class ToolCallEvent:
    """Fired before tool execution; handlers may block or rewrite params via ToolCallEventResult."""

    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent:
    """Fired after tool execution; handlers may override the result content
    via ToolResultEventResult.
    """

    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    is_error: bool = False


@dataclass
class SavePointEvent:
    """Fires after session writes are flushed — harness is idle and consistent."""

    type: Literal["save_point"] = field(default="save_point", init=False)


@dataclass
class SettledEvent:
    """Fires when the agent finishes a prompt() call with no more queued turns."""

    type: Literal["settled"] = field(default="settled", init=False)


@dataclass
class BeforeCompactionResult:
    """Return this from a before_compaction handler to cancel or replace the default algorithm."""

    cancel: bool = False
    compaction: CompactionResult | None = None


@dataclass
class BeforeCompactionEvent:
    """Fires before context compaction runs, before the LLM summarisation call."""

    type: Literal["before_compaction"] = field(default="before_compaction", init=False)
    preparation: CompactionPreparation | None = None
    entries: list[SessionEntry] = field(default_factory=list)
    manual: bool = False
    reason: CompactionReason = CompactionReason.Manual
    will_retry: bool = False


@dataclass
class CompactionStartEvent:
    """Fires when context compaction begins (auto or manual)."""

    type: Literal["compaction_start"] = field(default="compaction_start", init=False)
    manual: bool = False
    reason: CompactionReason = CompactionReason.Manual
    will_retry: bool = False


@dataclass
class CompactionEndEvent:
    """Fires when context compaction finishes (auto or manual)."""

    type: Literal["compaction_end"] = field(default="compaction_end", init=False)
    manual: bool = False
    tokens_before: int = 0
    summary_length: int = 0
    from_extension: bool = False
    reason: CompactionReason = CompactionReason.Manual
    will_retry: bool = False


@dataclass
class CompactionFailureEvent:
    """Fires when compaction fails."""

    type: Literal["compaction_failure"] = field(default="compaction_failure", init=False)
    manual: bool = False
    reason: CompactionReason = CompactionReason.Manual
    will_retry: bool = False
    error: str = ""


@dataclass
class CompactionCancelledEvent:
    """Fires when an extension cancels compaction."""

    type: Literal["compaction_cancelled"] = field(default="compaction_cancelled", init=False)
    manual: bool = False
    reason: CompactionReason = CompactionReason.Manual
    will_retry: bool = False


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class ContextEventResult:
    """Returned by context handlers to replace the message list sent to the LLM."""

    messages: list[Any] | None = None


@dataclass
class ToolCallEventResult:
    """Returned by tool_call handlers to block execution or rewrite invocation params."""

    block: bool = False
    reason: str | None = None
    params: dict[str, Any] | None = None


@dataclass
class ToolResultEventResult:
    """Returned by tool_result handlers to override content, error flag, or terminate the loop."""

    content: str | None = None
    is_error: bool | None = None
    terminate: bool = False
    metadata: dict | None = None


@dataclass
class MessageEndEventResult:
    """Returned by message_end handlers to swap the final AssistantMessage before it is stored."""

    message: Any | None = None


@dataclass
class BeforeAgentStartEventResult:
    """Returned by before_agent_start handlers to override the system prompt for this turn."""

    system_prompt: str | None = None
