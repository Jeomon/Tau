from __future__ import annotations

import asyncio
from asyncio import Queue
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM as LLM
    from tau.inference.types import ThinkingLevel
    from tau.tool.types import Tool

from tau.hooks.engine import (
    AgentEndEvent,
    AgentErrorEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageRollbackEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionFailureEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau.message.types import (
    AssistantMessage,
    LLMMessage,
    ToolCallContent,
    ToolResultContent,
    UserMessage,
)
from tau.tool.types import ToolExecutionMode, ToolInvocation, ToolResult


def _make_set_event() -> asyncio.Event:
    e = asyncio.Event()
    e.set()
    return e


AbortSignal = asyncio.Event
EmitEvent = Callable[["AgentEvent"], Awaitable[None]]


class SteeringMode(StrEnum):
    """Mode for delivering steering messages: one at a time or all at once."""

    OneAtATime = "one_at_a_time"
    All = "all"


class FollowupMode(StrEnum):
    """Mode for delivering follow-up messages: one at a time or all at once."""

    OneAtATime = "one_at_a_time"
    All = "all"


class AgentEventType(StrEnum):
    """Event type identifiers for agent and engine lifecycle events."""

    AgentStart = "agent_start"
    AgentEnd = "agent_end"
    TurnStart = "turn_start"
    TurnEnd = "turn_end"
    MessageStart = "message_start"
    MessageUpdate = "message_update"
    MessageEnd = "message_end"
    ToolExecutionStart = "tool_execution_start"
    ToolExecutionUpdate = "tool_execution_update"
    ToolExecutionEnd = "tool_execution_end"
    AgentError = "agent_error"


AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | MessageRollbackEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | ToolExecutionFailureEvent
    | AgentErrorEvent
)


@dataclass
class EngineContext:
    """Inputs for one standalone engine run."""

    system_prompt: str
    messages: list[LLMMessage]
    tools: list[Tool] = field(default_factory=list)


AfterToolCallCallback = Callable[
    [ToolInvocation, ToolResult, AbortSignal | None], Awaitable[ToolResult | None]
]
BeforeToolCallCallback = Callable[
    [ToolInvocation, AbortSignal | None], Awaitable[ToolInvocation | ToolResultContent | None]
]
EphemeralInjectionCallback = Callable[[], Awaitable[list[UserMessage]]]
GetFollowUpMessagesCallback = Callable[[], list[LLMMessage]]
GetSteeringMessagesCallback = Callable[[], list[LLMMessage]]
OnEventCallback = Callable[["AgentEvent"], Awaitable[None]]
ShouldSkipToolCallsCallback = Callable[[ToolCallContent], ToolResultContent]
ShouldStopAfterTurnCallback = Callable[[AssistantMessage, list[ToolResultContent]], bool]
TransformContextCallback = Callable[
    [list[LLMMessage], AbortSignal | None], Awaitable[list[LLMMessage]]
]


@dataclass
class EngineState:
    """Mutable runtime state shared between the Engine loop and external observers."""

    system_prompt: str | None = None
    messages: list[LLMMessage] = field(default_factory=list)
    pending_tool_calls: set[str] = field(default_factory=set)
    is_streaming: bool = False
    idle_event: asyncio.Event = field(default_factory=lambda: _make_set_event())
    llm: LLM | None = None
    streaming_message: AssistantMessage | None = None
    thinking_level: ThinkingLevel | None = None
    error_message: str | None = None
    tools: list[Tool] = field(default_factory=list)
    follow_up_queue: FollowupQueue | None = None
    steering_queue: SteeringQueue | None = None


@dataclass
class EngineOptions:
    """Engine behaviour knobs: hooks, execution strategy, and message injection callbacks."""

    after_tool_call: AfterToolCallCallback | None = None
    before_tool_call: BeforeToolCallCallback | None = None
    on_event: OnEventCallback | None = None
    execution_mode: ToolExecutionMode | None = None
    steering_mode: SteeringMode = SteeringMode.OneAtATime
    followup_mode: FollowupMode = FollowupMode.OneAtATime
    get_follow_up_messages: GetFollowUpMessagesCallback | None = None
    get_steering_messages: GetSteeringMessagesCallback | None = None
    should_stop_after_turn: ShouldStopAfterTurnCallback | None = None
    should_skip_tool_calls: ShouldSkipToolCallsCallback | None = None
    transform_context: TransformContextCallback | None = None
    ephemeral_injection: EphemeralInjectionCallback | None = None


@dataclass
class _MessageQueue:
    """Async FIFO queue for steering/follow-up messages with configurable drain behaviour."""

    mode: FollowupMode | SteeringMode
    queue: Queue[LLMMessage] = field(default_factory=Queue)

    def clear(self) -> None:
        """Clear all messages from the queue."""
        # Replace rather than drain to avoid blocking on an empty get() mid-loop.
        self.queue = Queue()

    async def enqueue(self, message: LLMMessage) -> None:
        """Add a message to the queue."""
        await self.queue.put(message)

    def is_empty(self) -> bool:
        """Check if the queue is empty."""
        return self.queue.empty()

    def snapshot(self) -> list[LLMMessage]:
        """Return a non-destructive copy of queued messages for inspection
        (e.g. QueueUpdateEvent).
        """
        return list(self.queue._queue)  # type: ignore[attr-defined]

    async def dequeue(self) -> list[LLMMessage]:
        """Drain one (OneAtATime) or all (All) messages from the queue."""
        messages: list[LLMMessage] = []
        if self.mode.value == "one_at_a_time":
            if not self.is_empty():
                messages.append(await self.queue.get())
        else:
            while not self.is_empty():
                messages.append(await self.queue.get())
        return messages


@dataclass
class FollowupQueue(_MessageQueue):
    """Queue for follow-up messages from external sources."""

    mode: FollowupMode  # type: ignore[assignment]


@dataclass
class SteeringQueue(_MessageQueue):
    """Queue for steering messages from external sources."""

    mode: SteeringMode  # type: ignore[assignment]
