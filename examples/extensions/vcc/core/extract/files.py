from __future__ import annotations

from dataclasses import dataclass, field

from ..blocks import Block
from ..tool_args import extract_path

# tau builtins are lowercase (read/write/edit); PascalCase kept for parity.
_FILE_READ_TOOLS = {"read", "Read", "read_file", "View"}
_FILE_WRITE_TOOLS = {"edit", "write", "Edit", "Write", "edit_file", "write_file", "MultiEdit"}
_FILE_CREATE_TOOLS = {"write", "Write", "write_file"}


@dataclass
class FileActivity:
    read: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    created: set[str] = field(default_factory=set)


def _longest_common_dir_prefix(paths: list[str]) -> str:
    """Longest common directory prefix among absolute paths ("" if < 2)."""
    abs_paths = [p for p in paths if p.startswith("/")]
    if len(abs_paths) < 2:
        return ""
    split = [p.split("/") for p in abs_paths]
    minlen = min(len(s) for s in split)
    i = 0
    while i < minlen - 1:
        seg = split[0][i]
        if not all(s[i] == seg for s in split):
            break
        i += 1
    if i < 2:
        return ""  # require at least /a/b common
    return "/".join(split[0][:i]) + "/"


def _trim_paths(paths: set[str], prefix: str) -> set[str]:
    if not prefix:
        return paths
    return {p[len(prefix):] if p.startswith(prefix) else p for p in paths}


def extract_files(blocks: list[Block]) -> FileActivity:
    act = FileActivity()
    for b in blocks:
        if b.kind != "tool_call":
            continue
        p = extract_path(b.args)
        if not p:
            continue
        if b.name in _FILE_READ_TOOLS:
            act.read.add(p)
        if b.name in _FILE_WRITE_TOOLS:
            act.modified.add(p)
        if b.name in _FILE_CREATE_TOOLS:
            act.created.add(p)

    all_paths = [*act.read, *act.modified, *act.created]
    prefix = _longest_common_dir_prefix(all_paths)
    if prefix:
        act.read = _trim_paths(act.read, prefix)
        act.modified = _trim_paths(act.modified, prefix)
        act.created = _trim_paths(act.created, prefix)
    return act
