from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tau.hooks.service import Hooks
from tau.inference.api.text.service import TextLLM
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.tool.registry import ToolRegistry


@dataclass(frozen=True)
class SettingsFactoryContext:
    """Inputs used to construct a settings manager."""

    cwd: Path
    config_dir: Path
    project_trusted: bool


@dataclass(frozen=True)
class LLMFactoryContext:
    """Inputs used to construct the runtime text LLM."""

    model_id: str
    provider: str | None
    settings: SettingsManager


@dataclass(frozen=True)
class SessionManagerFactoryContext:
    """Inputs used to construct session storage."""

    cwd: Path
    session_dir: Path | None
    session_file: Path | None
    persist: bool
    resume: bool


SettingsFactory = Callable[[SettingsFactoryContext], SettingsManager]
LLMFactory = Callable[[LLMFactoryContext], TextLLM]
SessionManagerFactory = Callable[[SessionManagerFactoryContext], SessionManager]
HooksFactory = Callable[[], Hooks]
ToolRegistryFactory = Callable[[], ToolRegistry]


@dataclass(frozen=True)
class RuntimeDependencies:
    """Optional factories for services constructed by a runtime."""

    settings: SettingsFactory | None = None
    llm: LLMFactory | None = None
    session_manager: SessionManagerFactory | None = None
    hooks: HooksFactory | None = None
    tool_registry: ToolRegistryFactory | None = None
