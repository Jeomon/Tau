from __future__ import annotations

import re
from pathlib import Path

_MAX_MEMORY_LINES = 200
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def resolve_memory_dir(agent_name: str, scope: str, cwd: Path) -> Path:
    """Resolve an agent memory directory without permitting path traversal."""
    if not _SAFE_NAME_RE.fullmatch(agent_name):
        raise ValueError(f"Unsafe agent name for memory directory: {agent_name!r}")
    if scope == "project":
        return cwd / ".tau" / "subagents" / "memory" / agent_name
    if scope == "local":
        return cwd / ".tau" / "subagents" / "memory-local" / agent_name
    if scope == "user":
        return Path.home() / ".tau" / "subagents" / "memory" / agent_name
    raise ValueError(f"Unknown memory scope: {scope!r}")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to use symlinked memory path: {path}")


def _read_memory_index(memory_dir: Path) -> str | None:
    _reject_symlink(memory_dir)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        return None
    _reject_symlink(memory_file)
    try:
        content = memory_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = content.splitlines()
    if len(lines) > _MAX_MEMORY_LINES:
        return "\n".join(lines[:_MAX_MEMORY_LINES]) + "\n… (truncated at 200 lines)"
    return content


def build_memory_block(
    *,
    agent_name: str,
    scope: str,
    cwd: Path,
    writable: bool,
) -> str:
    """Build the persistent-memory instructions injected into a subagent prompt."""
    memory_dir = resolve_memory_dir(agent_name, scope, cwd)
    if writable:
        memory_dir.mkdir(parents=True, exist_ok=True)
        _reject_symlink(memory_dir)

    existing = _read_memory_index(memory_dir)
    access = "read-write" if writable else "read-only"
    lines = [
        "# Agent Memory",
        "",
        f"Memory scope: {scope}",
        f"Memory directory: {memory_dir}",
        f"Access: {access}",
        "",
    ]
    if existing:
        lines.extend(["## Current MEMORY.md", existing, ""])
    else:
        lines.extend(["No MEMORY.md exists yet.", ""])

    if writable:
        lines.extend(
            [
                "## Memory Instructions",
                "",
                "- Keep MEMORY.md as a concise index under 200 lines.",
                "- Put detailed durable knowledge in separate Markdown files and link them.",
                "- Store verified, reusable knowledge; do not store secrets or temporary state.",
                "- Update or remove stale memories and avoid duplicates.",
            ]
        )
    else:
        lines.append("Use existing memory as context, but do not create or modify memory files.")
    return "\n".join(lines)
