"""Agent discovery: markdown files with frontmatter define subagent presets.

Discovery always merges three tiers by name — project (.tau/agents) wins
over user (~/.tau/agents), which wins over the builtin samples shipped
alongside this extension. Project agents are repo-controlled, so the tool
confirms before actually running one rather than gating discovery itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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


def agents_dir_for_scope(cwd: Path, scope: Literal["user", "project"]) -> Path:
    from tau.settings.paths import get_config_dir

    return get_config_dir(None if scope == "user" else cwd) / "agents"


def discover_agents(cwd: Path) -> tuple[list[AgentConfig], Path | None]:
    """Discover every agent visible from cwd. Returns (agents, project_agents_dir).

    Always merges all three tiers — builtin samples (bundled alongside this
    extension), user (~/.tau/agents), and project (.tau/agents). Project
    agents of the same name override user, which overrides builtin.
    """
    user_dir = agents_dir_for_scope(cwd, "user")
    project_dir = agents_dir_for_scope(cwd, "project")

    builtin_agents = _load_agents_from_dir(_BUILTIN_AGENTS_DIR, "builtin")
    user_agents = _load_agents_from_dir(user_dir, "user")
    project_agents = _load_agents_from_dir(project_dir, "project")

    merged: dict[str, AgentConfig] = {}
    for a in builtin_agents:
        merged[a.name] = a
    for a in user_agents:
        merged[a.name] = a
    for a in project_agents:
        merged[a.name] = a

    project_agents_dir = project_dir if project_dir.is_dir() else None
    return list(merged.values()), project_agents_dir
