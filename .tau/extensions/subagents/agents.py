from __future__ import annotations

import re
from pathlib import Path

from .types import AgentTypeDef

# ── Built-in agent types ───────────────────────────────────────────────────────

_BUILTIN_AGENTS: dict[str, AgentTypeDef] = {
    "general-purpose": AgentTypeDef(
        name="general-purpose",
        display_name="General purpose",
        description="Catch-all agent — inherits the parent's full system prompt and all tools.",
        system_prompt="",  # empty = inherit parent system prompt at spawn time
        tools="all",
        source="builtin",
    ),
    "scout": AgentTypeDef(
        name="scout",
        display_name="Scout",
        description="Fast read-only explorer. Use before you understand the codebase.",
        system_prompt=(
            "You are a scout agent. Your only job is to explore and report — "
            "never modify files. Read source files, search for symbols, list "
            "directories, and return a concise structured summary of what you found. "
            "Do not attempt fixes or suggestions beyond what was asked."
        ),
        tools=["read", "grep", "glob", "ls"],
        source="builtin",
    ),
    "researcher": AgentTypeDef(
        name="researcher",
        display_name="Researcher",
        description="Investigates external facts, docs, or web context before you trust them.",
        system_prompt=(
            "You are a researcher agent. Gather information, read files, and "
            "synthesise findings into a clear, cited summary. Prefer primary "
            "sources. Do not modify any files. Report exactly what you found."
        ),
        tools=["read", "grep", "glob", "ls"],
        source="builtin",
    ),
    "planner": AgentTypeDef(
        name="planner",
        display_name="Planner",
        description="Designs an implementation plan before a bigger change. Read-only.",
        system_prompt=(
            "You are a planner agent. Analyse the codebase, understand the "
            "requirements, and produce a step-by-step implementation plan. "
            "Identify risks, dependencies, and the exact files that need to change. "
            "Do not write or edit any files — your output is a plan only."
        ),
        tools=["read", "grep", "glob", "ls"],
        source="builtin",
    ),
    "worker": AgentTypeDef(
        name="worker",
        display_name="Worker",
        description="Implements a specific task with full read/write access.",
        system_prompt=(
            "You are a worker agent. Implement the task given to you precisely "
            "and completely. Read what you need, write and edit files, run terminal "
            "commands as required. When done, summarise exactly what you changed."
        ),
        tools="all",
        source="builtin",
    ),
    "reviewer": AgentTypeDef(
        name="reviewer",
        display_name="Reviewer",
        description="Reviews code or a plan for correctness, bugs, and quality.",
        system_prompt=(
            "You are a code reviewer agent. Read the specified files or diff and "
            "identify correctness bugs, logic errors, security issues, and "
            "simplification opportunities. Be specific — include file paths and "
            "line numbers. Do not modify any files."
        ),
        tools=["read", "grep", "glob", "ls"],
        source="builtin",
    ),
    "oracle": AgentTypeDef(
        name="oracle",
        display_name="Oracle",
        description="Second opinion on a risky decision before you commit to it.",
        system_prompt=(
            "You are an oracle agent providing a second opinion. Analyse the "
            "decision, approach, or design presented to you. Challenge assumptions, "
            "identify risks the proposer may have missed, and give a clear verdict: "
            "safe to proceed, proceed with caution, or do not proceed — with reasons."
        ),
        tools=["read", "grep", "glob", "ls"],
        source="builtin",
    ),
}

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

    return AgentTypeDef(
        name=name,
        display_name=display_name,
        description=description,
        system_prompt=body,
        tools=tools,
        model=fm.get("model"),
        max_turns=_int_or_none(fm.get("max_turns") or fm.get("maxTurns")),
        run_in_background=_bool(fm.get("run_in_background") or fm.get("runInBackground"), False),
        inherit_context=_bool(fm.get("inherit_context") or fm.get("inheritContext"), False),
        isolated=_bool(fm.get("isolated"), False),
        enabled=_bool(fm.get("enabled"), True),
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


def _int_or_none(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val))
    except (TypeError, ValueError):
        return None


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
        result.update(_BUILTIN_AGENTS)

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
