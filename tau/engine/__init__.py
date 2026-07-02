"""Standalone LLM streaming and tool-execution engine."""

from tau.engine.service import Engine
from tau.engine.types import (
    AgentEndEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentEventType,
    AgentStartEvent,
    EngineContext,
    EngineOptions,
    EngineState,
    FollowupMode,
    FollowupQueue,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    SteeringMode,
    SteeringQueue,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau.tool.types import ToolExecutionMode

# Compatibility aliases for the original public names.
Agent = Engine
AgentState = EngineState
AgentOptions = EngineOptions

__all__ = [
    "Engine",
    "EngineContext",
    "EngineState",
    "EngineOptions",
    "Agent",
    "AgentState",
    "AgentOptions",
    "AgentEvent",
    "AgentEventType",
    "AgentStartEvent",
    "AgentEndEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "MessageEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolExecutionEndEvent",
    "AgentErrorEvent",
    "ToolExecutionMode",
    "SteeringMode",
    "FollowupMode",
    "FollowupQueue",
    "SteeringQueue",
]
