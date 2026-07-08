"""Workflow file model: parsing and validating .tau/workflows/*.yaml files.

A workflow is a static, declarative description — not a script. It has no
loops, conditionals, or code execution: just an ordered list of phases, each
running one or more subagent tasks, optionally fanning out over a prior
result via `for_each`. See store._TEMPLATE for the exact shape handed to
users via "+ New workflow".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkflowParseError(ValueError):
    """Raised when a workflow YAML file is malformed."""


@dataclass
class WorkflowTask:
    agent: str
    task: str
    label: str | None = None
    schema: dict[str, Any] | None = None


@dataclass
class WorkflowPhase:
    title: str
    tasks: list[WorkflowTask]
    parallel: bool = False
    for_each: str | None = None


@dataclass
class WorkflowMeta:
    name: str
    description: str = ""


@dataclass
class WorkflowDef:
    meta: WorkflowMeta
    phases: list[WorkflowPhase]
    enabled: bool = True
    path: Path | None = None

    @property
    def slug(self) -> str:
        """Filename stem — the identifier used for lookups and /workflow <name>."""
        return self.path.stem if self.path else self.meta.name


def _require_str(d: dict, key: str, ctx: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowParseError(f"{ctx}: '{key}' must be a non-empty string")
    return value.strip()


def _parse_task(raw: Any, ctx: str) -> WorkflowTask:
    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{ctx}: each task must be a mapping")
    agent = _require_str(raw, "agent", ctx)
    task = _require_str(raw, "task", ctx)
    label = raw.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise WorkflowParseError(f"{ctx}: 'label' must be a non-empty string when set")
    schema = raw.get("schema")
    if schema is not None and not isinstance(schema, dict):
        raise WorkflowParseError(f"{ctx}: 'schema' must be a mapping (JSON Schema) when set")
    return WorkflowTask(
        agent=agent, task=task, label=label.strip() if label else None, schema=schema
    )


def _parse_phase(raw: Any, index: int) -> WorkflowPhase:
    ctx = f"phases[{index}]"
    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{ctx}: must be a mapping")
    title = _require_str(raw, "title", ctx)
    parallel = bool(raw.get("parallel", False))
    for_each = raw.get("for_each")
    if for_each is not None and not isinstance(for_each, str):
        raise WorkflowParseError(f"{ctx}: 'for_each' must be a string")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise WorkflowParseError(f"{ctx}: 'tasks' must be a non-empty list")
    tasks = [_parse_task(t, f"{ctx}.tasks[{i}]") for i, t in enumerate(tasks_raw)]

    if for_each and len(tasks) != 1:
        raise WorkflowParseError(f"{ctx}: 'for_each' requires exactly one task template")

    return WorkflowPhase(title=title, tasks=tasks, parallel=parallel, for_each=for_each)


def parse_workflow(path: Path) -> WorkflowDef:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowParseError(f"{path.name}: invalid YAML — {e}") from e

    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{path.name}: top level must be a mapping")

    meta_raw = raw.get("meta")
    if not isinstance(meta_raw, dict):
        raise WorkflowParseError(f"{path.name}: 'meta' is required")
    name = _require_str(meta_raw, "name", "meta")
    description = str(meta_raw.get("description") or "").strip()

    phases_raw = raw.get("phases")
    if not isinstance(phases_raw, list) or not phases_raw:
        raise WorkflowParseError(f"{path.name}: 'phases' must be a non-empty list")
    phases = [_parse_phase(p, i) for i, p in enumerate(phases_raw)]

    labels = [t.label for phase in phases for t in phase.tasks if t.label]
    dupes = {label for label in labels if labels.count(label) > 1}
    if dupes:
        raise WorkflowParseError(f"{path.name}: duplicate task labels: {', '.join(sorted(dupes))}")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WorkflowParseError(f"{path.name}: 'enabled' must be a boolean")

    return WorkflowDef(
        meta=WorkflowMeta(name=name, description=description),
        phases=phases,
        enabled=enabled,
        path=path,
    )
