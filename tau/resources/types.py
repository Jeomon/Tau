from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tau.hooks.service import Hooks
    from tau.settings.manager import SettingsManager
    from tau.settings.types import ExtensionEntry


@dataclass(frozen=True)
class ResourceContext:
    """Runtime services available while discovering and applying resources."""

    cwd: Path
    settings: SettingsManager
    hooks: Hooks
    load_context_files: bool = True


@dataclass(frozen=True)
class ContextFile:
    """Context instructions loaded from a project file."""

    path: Path
    content: str


@dataclass(frozen=True)
class ResourceDiagnostic:
    """Structured warning or error produced while loading resources."""

    severity: Literal["warning", "error"]
    message: str
    source: str = "resource-loader"
    path: Path | None = None


@dataclass(frozen=True)
class ResourceSnapshot:
    """Complete set of resource locations discovered for one runtime load."""

    builtins_extension_dir: Path
    project_extension_dir: Path | None = None
    global_extension_dir: Path | None = None
    extension_entries: tuple[ExtensionEntry, ...] = ()
    extension_sources: dict[str, str] = field(default_factory=dict)
    disabled_extension_stems: frozenset[str] = frozenset()
    extension_configs: dict[str, dict] = field(default_factory=dict)
    skill_paths: tuple[Path, ...] = ()
    prompt_paths: tuple[Path, ...] = ()
    theme_paths: tuple[Path, ...] = ()
    context_files: tuple[ContextFile, ...] = ()
    system_prompt: str | None = None
    diagnostics: tuple[ResourceDiagnostic, ...] = ()
