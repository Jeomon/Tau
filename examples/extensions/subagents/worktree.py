from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeInfo:
    repository: Path
    path: Path
    cwd: Path
    branch: str


async def _git(*args: str, cwd: Path) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return process.returncode or 0, stdout.decode("utf-8", errors="replace").strip()


async def create_worktree(cwd: Path, agent_id: str) -> WorktreeInfo:
    """Create a strict isolated worktree and return its mapped working directory."""
    code, root_raw = await _git("rev-parse", "--show-toplevel", cwd=cwd)
    if code != 0:
        raise RuntimeError(f"Worktree isolation requires a Git repository: {root_raw}")
    repository = Path(root_raw).resolve()
    relative = cwd.resolve().relative_to(repository)
    branch = f"tau-agent-{agent_id}"
    base = Path(tempfile.gettempdir()) / "tau-subagents"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{repository.name}-{agent_id}"
    if path.exists():
        raise RuntimeError(f"Worktree path already exists: {path}")
    code, output = await _git(
        "worktree",
        "add",
        "-b",
        branch,
        str(path),
        "HEAD",
        cwd=repository,
    )
    if code != 0:
        raise RuntimeError(f"Failed to create worktree: {output}")
    return WorktreeInfo(
        repository=repository,
        path=path,
        cwd=path / relative,
        branch=branch,
    )


async def finalize_worktree(info: WorktreeInfo, description: str) -> tuple[bool, str | None]:
    """Commit changes when present and remove the checkout while retaining its branch."""
    code, output = await _git("add", "-A", cwd=info.path)
    if code != 0:
        return False, f"Failed to stage worktree changes: {output}"
    code, _ = await _git("diff", "--cached", "--quiet", cwd=info.path)
    changed = code != 0
    if changed:
        message = f"subagent: {description.strip() or info.branch}"
        code, output = await _git("commit", "-m", message, cwd=info.path)
        if code != 0:
            return False, f"Failed to commit worktree changes: {output}"
    code, output = await _git("worktree", "remove", "--force", str(info.path), cwd=info.repository)
    if code != 0:
        return False, f"Failed to remove worktree: {output}"
    return changed, None
