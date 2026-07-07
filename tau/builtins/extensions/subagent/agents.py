"""Agent discovery: markdown files with YAML-ish frontmatter define subagent presets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AgentScope = Literal["user", "project", "both"]


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


def discover_agents(cwd: Path, scope: AgentScope) -> tuple[list[AgentConfig], Path | None]:
    """Discover agents for the given scope. Returns (agents, project_agents_dir).

    Builtin sample agents (bundled alongside this extension) always load as
    the base layer, regardless of scope — they're shipped defaults, not
    project-controlled content. User and project agents of the same name
    override them.
    """
    from tau.settings.paths import get_config_dir

    user_dir = get_config_dir(None) / "agents"
    project_dir = get_config_dir(cwd) / "agents"

    builtin_agents = _load_agents_from_dir(_BUILTIN_AGENTS_DIR, "builtin")
    user_agents = [] if scope == "project" else _load_agents_from_dir(user_dir, "user")
    project_agents = [] if scope == "user" else _load_agents_from_dir(project_dir, "project")

    merged: dict[str, AgentConfig] = {}
    for a in builtin_agents:
        merged[a.name] = a
    for a in user_agents:
        merged[a.name] = a
    for a in project_agents:
        merged[a.name] = a

    project_agents_dir = project_dir if project_dir.is_dir() else None
    return list(merged.values()), project_agents_dir
