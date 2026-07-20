from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tau.engine.types import EngineContext
from tau.session.compaction import DEFAULT_COMPACTION_SETTINGS, CompactionSettings
from tau.session.types import MessageMeta


class AgentPhase(StrEnum):
    """Agent execution phase."""

    IDLE = "idle"
    TURN = "turn"
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branch_summary"


# Compatibility alias. Turn inputs belong to the standalone engine package.
AgentContext = EngineContext


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
    # str members carry the JSON-friendly forms UserMessage.with_media accepts:
    # image URLs / base64 strings, and 'file:' paths or base64 for audio/video/file.
    # This lets non-bytes callers (e.g. the RPC layer) supply media too.
    images: list[bytes | str] = []
    audio: list[bytes | str] = []
    video: list[bytes | str] = []
    file: list[bytes | str] = []


@dataclass
class ContextUsage:
    """Token usage and context window statistics."""

    tokens: int
    context_window: int
    percent: float | None = None
