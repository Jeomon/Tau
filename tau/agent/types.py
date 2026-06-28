from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from tau.message.types import LLMMessage
from tau.session.compaction import DEFAULT_COMPACTION_SETTINGS, CompactionSettings
from tau.session.types import MessageMeta

if TYPE_CHECKING:
    from tau.tool.types import Tool


class AgentPhase(StrEnum):
    """Agent execution phase."""

    IDLE = "idle"
    TURN = "turn"
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branch_summary"


@dataclass
class AgentContext:
    """Snapshot of everything the LLM receives for one turn."""

    system_prompt: str
    messages: list[LLMMessage]
    tools: list[Tool] = field(default_factory=list)


class AgentConfig(BaseModel):
    """Internal runtime config passed to Agent.__init__."""

    model_config = {"arbitrary_types_allowed": True}

    cwd: Path
    system_prompt: str = ""
    model: Any | None = None
    context_window: int = 200_000
    compaction: CompactionSettings = DEFAULT_COMPACTION_SETTINGS


class PromptOptions(BaseModel):
    """Configuration options for prompt submission."""

    model_config = {"arbitrary_types_allowed": True}

    meta: MessageMeta | None = None
    images: list[bytes] = []
    audio: list[bytes] = []
    video: list[bytes] = []


@dataclass
class ContextUsage:
    """Token usage and context window statistics."""

    tokens: int
    context_window: int
    percent: float | None = None
