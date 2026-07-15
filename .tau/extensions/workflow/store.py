"""Discovery and file operations for .tau/workflows/*.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from model import WorkflowDef, WorkflowParseError, parse_workflow  # type: ignore[import-not-found]

_TEMPLATE = """\
meta:
  name: {name}
  description: Describe what this workflow does

enabled: true

phases:
  - title: Scan
    tasks:
      - agent: scout
        task: "Describe the first task here"
        label: scan

  - title: Synthesize
    tasks:
      - agent: worker
        task: "Summarize: {{previous}}"
        label: summary
"""

_SAFE_NAME_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass
class BrokenWorkflow:
    path: Path
    error: str


def workflows_dir(cwd: Path) -> Path:
    return Path(cwd) / ".tau" / "workflows"


def discover(cwd: Path) -> tuple[list[WorkflowDef], list[BrokenWorkflow]]:
    directory = workflows_dir(cwd)
    valid: list[WorkflowDef] = []
    broken: list[BrokenWorkflow] = []
    if not directory.is_dir():
        return valid, broken
    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    for path in paths:
        try:
            valid.append(parse_workflow(path))
        except WorkflowParseError as e:
            broken.append(BrokenWorkflow(path=path, error=str(e)))
    return valid, broken


def find(cwd: Path, name: str) -> WorkflowDef | None:
    valid, _ = discover(cwd)
    lowered = name.strip().lower()
    for wf in valid:
        if wf.slug.lower() == lowered or wf.meta.name.lower() == lowered:
            return wf
    return None


def slugify(name: str) -> str:
    slug = _SAFE_NAME_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "workflow"


def create(cwd: Path, name: str) -> Path:
    directory = workflows_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    path = directory / f"{slug}.yaml"
    suffix = 2
    while path.exists():
        path = directory / f"{slug}-{suffix}.yaml"
        suffix += 1
    path.write_text(_TEMPLATE.format(name=slug))
    return path


def set_enabled(path: Path, enabled: bool) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["enabled"] = enabled
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def rename(path: Path, new_name: str) -> Path:
    directory = path.parent
    slug = slugify(new_name)
    new_path = directory / f"{slug}.yaml"
    if new_path.exists() and new_path != path:
        raise FileExistsError(f"A workflow named '{slug}' already exists.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = data.get("meta") or {}
    meta["name"] = slug
    data["meta"] = meta
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    path.rename(new_path)
    return new_path


def delete(path: Path) -> None:
    path.unlink(missing_ok=True)
