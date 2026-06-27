from __future__ import annotations

from pathlib import Path

from tau.settings.paths import CONFIG_DIR_NAME
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
    - A ``.tau/`` local config directory
    """
    current = Path(normalize(cwd))
    while True:
        if (current / CONFIG_DIR_NAME).exists():
            return True
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
