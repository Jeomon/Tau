"""Agent discovery and management: markdown files with frontmatter define subagent presets.

Discovery merges three tiers (builtin, user, project) by name — project wins
over user, user wins over builtin. Management actions (create/update/delete/
eject/disable/enable/reset) mutate the user/project markdown files directly —
there is no separate database, the files on disk are the source of truth.
Builtin sample agents are shipped read-only; disable/eject exist precisely so
they can still be hidden or forked into an editable copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AgentScope = Literal["user", "project", "both"]
TargetScope = Literal["user", "project"]

_DISABLED_FILE = ".disabled.json"


@dataclass
class AgentConfig:
    name: str
    description: str
    tools: list[str] | None
    model: str | None
    system_prompt: str
    source: Literal["builtin", "user", "project"]
    file_path: str


_BUILTIN_AGENTS_DIR = Path(__file__).parent / "agents"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    text = text.lstrip("\n")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    return meta, body


def _render_frontmatter(
    name: str, description: str, tools: list[str] | None, model: str | None, system_prompt: str
) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if tools:
        lines.append(f"tools: {', '.join(tools)}")
    if model:
        lines.append(f"model: {model}")
    lines.append("---")
    lines.append("")
    lines.append(system_prompt.strip())
    lines.append("")
    return "\n".join(lines)


def _load_agents_from_dir(
    directory: Path, source: Literal["builtin", "user", "project"]
) -> list[AgentConfig]:
    agents: list[AgentConfig] = []
    if not directory.is_dir():
        return agents

    for path in sorted(directory.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        frontmatter, body = _parse_frontmatter(content)
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not name or not description:
            continue

        tools_raw = frontmatter.get("tools")
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()] if tools_raw else None

        agents.append(
            AgentConfig(
                name=name,
                description=description,
                tools=tools or None,
                model=frontmatter.get("model") or None,
                system_prompt=body,
                source=source,
                file_path=str(path),
            )
        )

    return agents


def _load_disabled(agents_dir: Path) -> set[str]:
    path = agents_dir / _DISABLED_FILE
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(n) for n in data} if isinstance(data, list) else set()


def _save_disabled(agents_dir: Path, names: set[str]) -> None:
    path = agents_dir / _DISABLED_FILE
    if not names:
        path.unlink(missing_ok=True)
        return
    agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(names), indent=2) + "\n", encoding="utf-8")


def agents_dir_for_scope(cwd: Path, scope: TargetScope) -> Path:
    from tau.settings.paths import get_config_dir

    return get_config_dir(None if scope == "user" else cwd) / "agents"


def discover_agents(cwd: Path, scope: AgentScope) -> tuple[list[AgentConfig], Path | None]:
    """Discover agents for the given scope. Returns (agents, project_agents_dir).

    Builtin sample agents (bundled alongside this extension) always load as
    the base layer, regardless of scope — they're shipped defaults, not
    project-controlled content. User and project agents of the same name
    override them; a name in either scope's disabled set is excluded
    entirely, regardless of which tier it came from.
    """
    user_dir = agents_dir_for_scope(cwd, "user")
    project_dir = agents_dir_for_scope(cwd, "project")

    builtin_agents = _load_agents_from_dir(_BUILTIN_AGENTS_DIR, "builtin")
    user_agents = [] if scope == "project" else _load_agents_from_dir(user_dir, "user")
    project_agents = [] if scope == "user" else _load_agents_from_dir(project_dir, "project")

    disabled: set[str] = set()
    if scope != "project":
        disabled |= _load_disabled(user_dir)
    if scope != "user":
        disabled |= _load_disabled(project_dir)

    merged: dict[str, AgentConfig] = {}
    for a in builtin_agents:
        merged[a.name] = a
    for a in user_agents:
        merged[a.name] = a
    for a in project_agents:
        merged[a.name] = a
    for name in disabled:
        merged.pop(name, None)

    project_agents_dir = project_dir if project_dir.is_dir() else None
    return list(merged.values()), project_agents_dir


# ── Management (create/update/delete/eject/disable/enable/reset) ───────────


def create_agent(
    cwd: Path,
    scope: TargetScope,
    name: str,
    description: str,
    system_prompt: str,
    tools: list[str] | None,
    model: str | None,
) -> str:
    """Write a new agent file. Raises FileExistsError if one already exists in this scope."""
    agents_dir = agents_dir_for_scope(cwd, scope)
    path = agents_dir / f"{name}.md"
    if path.exists():
        raise FileExistsError(str(path))
    agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_frontmatter(name, description, tools, model, system_prompt), encoding="utf-8"
    )
    return str(path)


def update_agent(
    cwd: Path,
    scope: TargetScope,
    name: str,
    *,
    description: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None | Literal["__unset__"] = "__unset__",
    model: str | None | Literal["__unset__"] = "__unset__",
) -> str:
    """Merge changes into an existing agent file in this scope.

    Raises FileNotFoundError if absent.
    """
    agents_dir = agents_dir_for_scope(cwd, scope)
    path = agents_dir / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(str(path))

    frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    new_description = description if description is not None else frontmatter.get("description", "")
    new_system_prompt = system_prompt if system_prompt is not None else body
    if tools == "__unset__":
        tools_raw = frontmatter.get("tools")
        new_tools = [t.strip() for t in tools_raw.split(",") if t.strip()] if tools_raw else None
    else:
        new_tools = tools
    new_model = frontmatter.get("model") or None if model == "__unset__" else model

    path.write_text(
        _render_frontmatter(name, new_description, new_tools, new_model, new_system_prompt),
        encoding="utf-8",
    )
    return str(path)


def delete_agent(cwd: Path, scope: TargetScope, name: str) -> str:
    """Delete an agent file in this scope. Raises FileNotFoundError if absent."""
    agents_dir = agents_dir_for_scope(cwd, scope)
    path = agents_dir / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    path.unlink()
    return str(path)


def eject_agent(cwd: Path, scope: TargetScope, agent: AgentConfig) -> str:
    """Copy an existing agent's definition into this scope as an editable file.

    Works regardless of the agent's current source (builtin, user, project);
    the copy shadows the original by name once loaded from this scope.
    """
    agents_dir = agents_dir_for_scope(cwd, scope)
    path = agents_dir / f"{agent.name}.md"
    if path.exists():
        raise FileExistsError(str(path))
    agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_frontmatter(
            agent.name, agent.description, agent.tools, agent.model, agent.system_prompt
        ),
        encoding="utf-8",
    )
    return str(path)


def disable_agent(cwd: Path, scope: TargetScope, name: str) -> bool:
    """Add name to this scope's disabled set. Returns True if it changed anything."""
    agents_dir = agents_dir_for_scope(cwd, scope)
    disabled = _load_disabled(agents_dir)
    if name in disabled:
        return False
    disabled.add(name)
    _save_disabled(agents_dir, disabled)
    return True


def enable_agent(cwd: Path, scope: TargetScope, name: str) -> bool:
    """Remove name from this scope's disabled set. Returns True if it changed anything."""
    agents_dir = agents_dir_for_scope(cwd, scope)
    disabled = _load_disabled(agents_dir)
    if name not in disabled:
        return False
    disabled.discard(name)
    _save_disabled(agents_dir, disabled)
    return True


def reset_agent(cwd: Path, scope: TargetScope, name: str) -> tuple[bool, bool]:
    """Delete the scope's custom file (if any) and clear any disabled override.

    Returns (file_removed, was_disabled).
    """
    agents_dir = agents_dir_for_scope(cwd, scope)
    path = agents_dir / f"{name}.md"
    file_removed = path.is_file()
    if file_removed:
        path.unlink()
    was_disabled = enable_agent(cwd, scope, name)
    return file_removed, was_disabled
