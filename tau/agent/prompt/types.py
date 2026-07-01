from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.resources.types import ContextFile
from tau.tool.types import Tool


class PromptOptions(BaseModel):
    """Options for constructing the system prompt."""

    model_config = {"arbitrary_types_allowed": True}

    cwd: Path
    tools: list[Tool] = Field(default_factory=list)

    # Identity-layer override. Unlike RuntimeConfig.system_prompt, this does not
    # bypass the remaining generated prompt sections.
    identity_prompt: str | None = None

    # Appended verbatim after all generated sections (APPEND_SYSTEM.md).
    append_prompt: str | None = None

    # Extra strings appended after append_prompt (used by extensions).
    extra_appends: list[str] = Field(default_factory=list)

    # Skills to list in the system prompt as <available_skills>.
    skills: list[Any] = Field(default_factory=list)

    # Disable auto-discovery of AGENTS.md and CLAUDE.md from project directory.
    disable_context_files: bool = Field(default=False)

    # Whether the project directory is trusted (for loading extensions, settings, context files).
    # None = auto-detect from trust store.
    project_trusted: bool | None = Field(default=None)

    # Preloaded context files. None keeps direct PromptBuilder auto-discovery.
    context_files: tuple[ContextFile, ...] | None = None
