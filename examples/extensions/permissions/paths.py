"""Path canonicalization and containment.

A path rule is only as good as the set of spellings it is matched against. The
agent can name one file as ``src/App.jsx``, ``./src/App.jsx``,
``/abs/project/src/App.jsx``, or through a symlink that lands somewhere else
entirely. :class:`AccessPath` derives every one of those and matches rules
against all of them, so a rule cannot be dodged by choosing a spelling.

The symlink case matters most: without resolution, ``ln -s ~/.ssh/id_rsa ./k``
followed by ``read k`` walks straight past a ``~/.ssh/*`` deny.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Paths that are always denied for writes regardless of configuration, because
#: an agent that can edit these can edit its own policy.
SELF_PROTECTED_SUFFIXES = (
    ".tau/extensions/permissions/config.json",
    ".tau/extensions/permissions/decisions.log",
)


def _tilde(path: str, home: Path) -> str:
    """Re-express an absolute path under home as ``~/...``.

    Rules are written with ``~``, so producing this spelling lets
    ``~/.ssh/*`` match a path the agent gave as ``/Users/x/.ssh/id_rsa``.
    """
    try:
        rel = Path(path).relative_to(home)
    except ValueError:
        return ""
    return f"~/{rel.as_posix()}"


@dataclass(frozen=True)
class AccessPath:
    """One filesystem target, in every spelling a rule might be written in."""

    raw: str
    absolute: str
    real: str
    tilde: str
    real_tilde: str

    @classmethod
    def build(cls, raw: str, cwd: Path, home: Path | None = None) -> AccessPath:
        home = home or Path.home()
        expanded = os.path.expanduser(raw)
        absolute = os.path.normpath(expanded if os.path.isabs(expanded) else str(cwd / expanded))
        try:
            real = os.path.realpath(absolute)
        except OSError:
            # A broken symlink or a permission error on an intermediate
            # directory must not fail open — fall back to the literal path.
            real = absolute
        return cls(
            raw=raw,
            absolute=absolute,
            real=real,
            tilde=_tilde(absolute, home),
            real_tilde=_tilde(real, home),
        )

    def match_values(self) -> list[str]:
        """Every spelling a pattern may be tested against, deduplicated."""
        seen: list[str] = []
        for value in (self.raw, self.absolute, self.real, self.tilde, self.real_tilde):
            if value and value not in seen:
                seen.append(value)
        return seen

    def escapes(self, root: Path) -> bool:
        """True when neither the literal nor the resolved path is under ``root``.

        Both are checked: the literal catches ``../../etc/passwd``, the resolved
        catches a symlink inside the project pointing outside it.
        """
        root_real = os.path.realpath(str(root))
        # Both spellings must be inside. Using `or` here would mean a symlink
        # sitting at `project/link.txt` counted as contained purely because its
        # literal path is inside the project, which is exactly the evasion the
        # resolved path exists to catch.
        literal_inside = _within(self.absolute, str(root)) or _within(self.absolute, root_real)
        real_inside = _within(self.real, root_real) or _within(self.real, str(root))
        return not (literal_inside and real_inside)

    def is_self_protected(self, root: Path) -> bool:
        """True when this path is the extension's own config or decision log."""
        for suffix in SELF_PROTECTED_SUFFIXES:
            if self.absolute.endswith(suffix) or self.real.endswith(suffix):
                return True
        return False


def _within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or sits underneath it."""
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


#: Which parameter of each built-in tool carries a filesystem path, and whether
#: reaching it counts as a write. Extension tools fall back to ``path``.
PATH_PARAMS: dict[str, tuple[str, bool]] = {
    "read": ("path", False),
    "write": ("path", True),
    "edit": ("path", True),
    "ls": ("path", False),
    "glob": ("path", False),
    "grep": ("path", False),
}


def extract_path(tool_name: str, params: dict) -> tuple[str | None, bool]:
    """Return ``(path, is_write)`` for a tool call, or ``(None, False)``.

    Unknown tools are probed for a ``path`` key so extension and MCP tools that
    follow the convention are gated too, rather than silently bypassing rules.
    """
    known = PATH_PARAMS.get(tool_name)
    if known is not None:
        key, is_write = known
        value = params.get(key)
        return (value if isinstance(value, str) and value else None), is_write

    value = params.get("path")
    if isinstance(value, str) and value:
        return value, False
    nested = params.get("arguments")
    if isinstance(nested, dict):
        inner = nested.get("path")
        if isinstance(inner, str) and inner:
            return inner, False
    return None, False
