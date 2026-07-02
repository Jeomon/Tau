from __future__ import annotations

import re
from dataclasses import dataclass

from ..blocks import Block

_COMMIT_MSG_RE = re.compile(
    r"""git\s+commit[^\n]*?-m\s+(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|\$?'((?:[^'\\]|\\.)*)')"""
)
_HASH_RE = re.compile(r"\b([0-9a-f]{7,12})\b")
_BRACKET_RE = re.compile(r"\[\S+\s+([0-9a-f]{7,12})\]")
_RANGE_RE = re.compile(r"\b([0-9a-f]{7,12})\.\.([0-9a-f]{7,12})\b")

# tau runs shell via the "terminal" tool; "bash"/"Bash" kept for parity.
_SHELL_TOOLS = {"terminal", "bash", "Bash"}


@dataclass
class CommitInfo:
    message: str
    hash: str | None = None


def _first_line(text: str) -> str:
    return re.split(r"\\n|\n", text, maxsplit=1)[0].strip()


def _clean_message(msg: str) -> str:
    return msg.replace('\\"', '"').replace("\\'", "'").strip()


def _hash_from_output(text: str) -> str | None:
    m = _BRACKET_RE.search(text)
    if m:
        return m.group(1)
    m = _RANGE_RE.search(text)
    if m:
        return m.group(2)
    m = _HASH_RE.search(text)
    if m:
        return m.group(1)
    return None


def _commit_command(block: Block) -> str | None:
    """Return the shell command from a commit-bearing block, else None."""
    if block.kind == "bash":
        cmd = block.command
    elif block.kind == "tool_call" and block.name in _SHELL_TOOLS:
        cmd = block.args.get("command") if isinstance(block.args.get("command"), str) else ""
    else:
        return None
    if cmd and re.search(r"\bgit\s+commit\b", cmd):
        return cmd
    return None


def extract_commits(blocks: list[Block]) -> list[CommitInfo]:
    commits: list[CommitInfo] = []

    for i, b in enumerate(blocks):
        cmd = _commit_command(b)
        if not cmd:
            continue
        m = _COMMIT_MSG_RE.search(cmd)
        if not m:
            continue
        message = _first_line(_clean_message(m.group(1) or m.group(2) or m.group(3) or ""))
        if not message:
            continue

        hash_: str | None = None
        # `!` bash blocks carry their own output; tool calls pair with the result.
        if b.kind == "bash" and b.output:
            hash_ = _hash_from_output(b.output)
        else:
            for j in range(i + 1, min(len(blocks), i + 3)):
                r = blocks[j]
                if r.kind != "tool_result":
                    continue
                hash_ = _hash_from_output(r.text)
                if hash_:
                    break

        key = f"{hash_ or ''}::{message}"
        if not any(f"{c.hash or ''}::{c.message}" == key for c in commits):
            commits.append(CommitInfo(message=message, hash=hash_))

    return commits


def format_commits(commits: list[CommitInfo], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for c in commits[-limit:]:  # keep most recent
        prefix = f"{c.hash}: " if c.hash else ""
        lines.append(f"{prefix}{c.message}")
    return lines
