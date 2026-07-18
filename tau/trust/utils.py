from __future__ import annotations

from pathlib import Path

from tau.settings.paths import CONFIG_DIR_NAME, CONFIG_DIR_PATH
from tau.trust.types import TrustOption


def normalize(cwd: str | Path) -> str:
    """Resolve *cwd* to an absolute POSIX string."""
    return str(Path(cwd).resolve())


def find_nearest(data: dict[str, bool | None], cwd: str) -> tuple[str, bool] | None:
    """Walk up from *cwd* and return ``(path, decision)`` for the closest stored entry."""
    current = normalize(cwd)
    while True:
        val = data.get(current)
        if val is True:
            return current, True
        if val is False:
            return current, False
        parent = str(Path(current).parent)
        if parent == current:
            return None
        current = parent


def has_project_trust_inputs(cwd: str | Path) -> bool:
    """Return ``True`` if *cwd* (or any ancestor) contains files that require a trust decision.

    Specifically looks for:
    - A ``.tau/`` local config directory (the global ``~/.tau`` config dir is
      the user's own and never counts as a *project* trust input)
    - An ``.agents/skills/`` directory containing project-provided instructions
    - Context files (AGENTS.md / CLAUDE.md) in any directory the runtime would
      inject them from — git root down to cwd (see ``load_project_context_files``)
    """
    # Imported lazily: trust must not depend on the agent package at import time.
    from tau.agent.prompt.builder import _context_file_paths, _find_git_root

    global_config_dir = CONFIG_DIR_PATH.resolve()
    current = Path(normalize(cwd))
    git_root = _find_git_root(current)
    context_stop = git_root if git_root is not None else current
    in_context_range = True
    while True:
        config_dir = current / CONFIG_DIR_NAME
        if config_dir.exists() and config_dir.resolve() != global_config_dir:
            return True
        if (current / ".agents" / "skills").exists():
            return True
        if in_context_range and _context_file_paths(current):
            return True
        if current == context_stop:
            in_context_range = False
        parent = current.parent
        if parent == current:
            return False
        current = parent


def get_trust_options(cwd: str | Path, *, session_only: bool = True) -> list[TrustOption]:
    """Build the ordered list of trust choices to present to the user.

    Args:
        cwd: Project working directory.
        session_only: Include a "Trust (this session only)" option that does not
            persist the decision to disk.
    """
    resolved = normalize(cwd)
    parent = str(Path(resolved).parent)

    options: list[TrustOption] = [
        TrustOption(label="Trust", trusted=True, save_path=resolved),
    ]
    if parent != resolved:
        options.append(
            TrustOption(
                label=f"Trust parent folder ({parent})",
                trusted=True,
                save_path=parent,
                clear_child_path=resolved,
            )
        )
    if session_only:
        options.append(TrustOption(label="Trust (this session only)", trusted=True, save_path=None))
    options.append(TrustOption(label="Do not trust", trusted=False, save_path=resolved))
    return options
