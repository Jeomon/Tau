from __future__ import annotations

import re
from pathlib import Path

from .types import AgentTypeDef

_BUILTIN_AGENTS_DIR = Path(__file__).with_name("builtin_agents")

# ── Frontmatter parser ─────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def _parse_md(path: Path) -> AgentTypeDef | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    name = path.stem.lower()
    if m:
        fm_raw, body = m.group(1), m.group(2).strip()
        fm = _parse_yaml_simple(fm_raw)
    else:
        fm, body = {}, text.strip()

    if not body and not fm:
        return None

    display_name = fm.get("display_name") or fm.get("displayName") or name.replace("-", " ").title()
    description = fm.get("description", "")
    tools_raw = fm.get("tools", "all")
    tools: list[str] | str
    if isinstance(tools_raw, str):
        if tools_raw in ("all", "none", "*"):
            tools = tools_raw
        else:
            tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    else:
        tools = tools_raw
    disallowed_tools = _csv(fm.get("disallowed_tools") or fm.get("disallowedTools"))
    skills_raw = fm.get("skills")
    skills: list[str] | str
    if isinstance(skills_raw, bool):
        skills = "all" if skills_raw else []
    elif str(skills_raw).lower() in {"all", "true"}:
        skills = "all"
    else:
        skills = _csv(skills_raw)

    return AgentTypeDef(
        name=name,
        display_name=display_name,
        description=description,
        system_prompt=body,
        tools=tools,
        disallowed_tools=disallowed_tools,
        skills=skills,
        model=fm.get("model"),
        max_turns=_int_or_none(fm.get("max_turns") or fm.get("maxTurns")),
        run_in_background=_bool(fm.get("run_in_background") or fm.get("runInBackground"), False),
        inherit_context=_bool(fm.get("inherit_context") or fm.get("inheritContext"), False),
        isolated=_bool(fm.get("isolated"), False),
        isolation="worktree" if fm.get("isolation") == "worktree" else None,
        enabled=_bool(fm.get("enabled"), True),
        memory=_memory_scope_or_none(fm.get("memory")),
    )


def _parse_yaml_simple(text: str) -> dict:
    """Minimal YAML key: value parser (no nested, no lists)."""
    result: dict = {}
    for line in text.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.lower() == "true":
            result[key] = True
        elif val.lower() == "false":
            result[key] = False
        elif val.lower() in ("null", "~", ""):
            result[key] = None
        else:
            result[key] = val
    return result


def _bool(val: object, default: bool) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


def _csv(val: object) -> list[str]:
    if val is None:
        return []
    return [item.strip() for item in str(val).split(",") if item.strip()]


def _int_or_none(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val))
    except (TypeError, ValueError):
        return None


def _memory_scope_or_none(val: object) -> str | None:
    if val is None:
        return None
    scope = str(val).strip().lower()
    return scope if scope in {"project", "local", "user"} else None


# ── Public loader ──────────────────────────────────────────────────────────────


def load_agent_types(
    cwd: Path,
    disable_builtins: bool = False,
) -> dict[str, AgentTypeDef]:
    """
    Return merged agent type registry.
    Priority: project (.tau/subagents/agents/) > global (~/.tau/subagents/agents/) > builtin.
    """
    result: dict[str, AgentTypeDef] = {}

    if not disable_builtins:
        for md in sorted(_BUILTIN_AGENTS_DIR.glob("*.md")):
            agent = _parse_md(md)
            if agent:
                agent.source = "builtin"
                result[agent.name] = agent

    # Global agents
    global_dir = Path.home() / ".tau" / "subagents" / "agents"
    if global_dir.is_dir():
        for md in sorted(global_dir.glob("*.md")):
            agent = _parse_md(md)
            if agent:
                agent.source = "global"
                result[agent.name] = agent

    # Project agents (highest priority)
    project_dir = cwd / ".tau" / "subagents" / "agents"
    if project_dir.is_dir():
        for md in sorted(project_dir.glob("*.md")):
            agent = _parse_md(md)
            if agent:
                agent.source = "project"
                result[agent.name] = agent

    return result


def get_available_names(types: dict[str, AgentTypeDef]) -> list[str]:
    return [name for name, t in types.items() if t.enabled]
