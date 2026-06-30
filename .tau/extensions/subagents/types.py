from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class AgentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    STEERED = "steered"   # hit turn limit, wrapped up gracefully
    ABORTED = "aborted"   # exceeded grace period after turn limit
    STOPPED = "stopped"   # user interrupted
    ERROR = "error"


@dataclass
class AgentTypeDef:
    name: str
    display_name: str
    description: str
    system_prompt: str
    tools: list[str] | str = "all"   # "all", "none", list of tool names
    model: str | None = None
    max_turns: int | None = None
    run_in_background: bool = False
    inherit_context: bool = False
    isolated: bool = False
    enabled: bool = True
    source: str = "builtin"          # "builtin" | "project" | "global"


@dataclass
class AgentRecord:
    id: str
    agent_type: str
    description: str
    prompt: str
    status: AgentStatus
    model: str | None
    max_turns: int | None
    run_in_background: bool
    result: str | None = None
    error: str | None = None
    token_input: int = 0
    token_output: int = 0
    tool_uses: int = 0
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    output_file: Path | None = None
    task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    steering_queue: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int | None:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at) * 1000)
        return None

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_type": self.agent_type,
            "description": self.description,
            "status": self.status,
            "turn_count": self.turn_count,
            "tool_uses": self.tool_uses,
            "token_input": self.token_input,
            "token_output": self.token_output,
            "duration_ms": self.duration_ms,
        }
