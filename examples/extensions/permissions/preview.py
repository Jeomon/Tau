"""Diffs for the approval prompt.

Approving a `write` or `edit` without seeing what changes is approval in name
only — the interesting part of the decision is the content, not the path. So
the prompt renders a unified diff of what the call would do.

Everything here is best-effort and bounded. A preview that raises, blocks on a
huge file, or floods the prompt with 4000 lines would make the gate worse than
having no preview at all, so every failure degrades to "no diff shown" and the
output is capped.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

#: Files larger than this are not read for a diff. Approval should not stall on
#: a multi-megabyte read, and no one reviews a diff that big in a picker.
MAX_FILE_BYTES = 512 * 1024

#: Diff lines shown before truncating.
MAX_DIFF_LINES = 24

#: Longest single rendered line.
MAX_LINE = 160


def _read(path: Path) -> list[str] | None:
    """Existing lines, or ``None`` when there is nothing usable to diff."""
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # Unreadable or binary — the caller falls back to no diff rather than
        # guessing at content it cannot represent.
        return None


def _trim(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[: MAX_LINE - 1] + "…"


def _format(diff: list[str]) -> list[str]:
    """Trim a unified diff to something readable inside a prompt."""
    body = [line for line in diff if not line.startswith(("---", "+++"))]
    shown = [_trim(line.rstrip("\n")) for line in body[:MAX_DIFF_LINES]]
    hidden = len(body) - len(shown)
    if hidden > 0:
        shown.append(f"… {hidden} more diff line{'s' if hidden != 1 else ''}")
    return shown


def _anchor_line(anchor: str) -> int | None:
    """Line number out of a ``<line>:<hash>`` edit anchor."""
    head = anchor.split(":", 1)[0]
    try:
        value = int(head)
    except ValueError:
        return None
    return value if value > 0 else None


def read_lines(path: Path) -> list[str] | None:
    """Existing lines, or ``None`` when there is nothing usable to diff."""
    return _read(path)


def proposed_edit_lines(existing: list[str] | None, params: dict) -> list[str] | None:
    """The whole file as an ``edit`` would leave it, or ``None`` if unresolvable.

    ``edit_diff`` diffs only the replaced slice, which is enough to *show* a
    change but not to number it: the edit tool's own renderer derives line
    numbers from the hunk headers, so reusing it needs a diff taken against
    the whole file rather than the slice.
    """
    if existing is None:
        return None
    start = _anchor_line(str(params.get("start_anchor", "")))
    end = _anchor_line(str(params.get("end_anchor", "")))
    new_content = params.get("new_content")
    if start is None or end is None or not isinstance(new_content, str):
        return None
    if end < start:
        start, end = end, start
    return existing[: start - 1] + new_content.splitlines() + existing[end:]


def edit_diff(path: Path, params: dict) -> list[str]:
    """Diff for an `edit` call, resolving its hashline anchors to a range."""
    existing = _read(path)
    if existing is None:
        return []

    start = _anchor_line(str(params.get("start_anchor", "")))
    end = _anchor_line(str(params.get("end_anchor", "")))
    new_content = params.get("new_content")
    if start is None or end is None or not isinstance(new_content, str):
        return []
    if end < start:
        start, end = end, start

    # Anchors are 1-based and the range is inclusive.
    old_slice = existing[start - 1 : end]
    new_slice = new_content.splitlines()

    diff = list(difflib.unified_diff(old_slice, new_slice, lineterm="", n=1))
    return _format(diff)


def write_diff(path: Path, params: dict) -> tuple[str, list[str]]:
    """``(label, lines)`` for a `write` — a diff, or a preview when new."""
    content = params.get("content")
    if not isinstance(content, str):
        return "content", []
    new_lines = content.splitlines()

    existing = _read(path)
    if existing is None:
        # Creating a file, or replacing one we cannot read. Show the head of
        # what would be written so the size and shape are visible.
        head = [_trim(line) for line in new_lines[:MAX_DIFF_LINES]]
        hidden = len(new_lines) - len(head)
        if hidden > 0:
            head.append(f"… {hidden} more line{'s' if hidden != 1 else ''}")
        return "content", ([f"+ {line}" for line in head] if head else [])

    # Overwriting something that exists is a change, not a fresh file, and
    # labelling the diff "content" would misdescribe what is being approved.
    return "changes", _format(list(difflib.unified_diff(existing, new_lines, lineterm="", n=1)))


def content_summary(params: dict) -> str | None:
    """A one-line size summary for content-bearing calls."""
    content = params.get("new_content")
    if not isinstance(content, str):
        content = params.get("content")
    if not isinstance(content, str):
        return None
    lines = len(content.splitlines())
    chars = len(content)
    return f"{lines} line{'s' if lines != 1 else ''}, {chars} char{'s' if chars != 1 else ''}"


def build(tool_name: str, raw_path: str, cwd: Path, params: dict) -> tuple[str, list[str]]:
    """``(label, lines)`` for a tool call; empty lines when there is nothing."""
    try:
        path = Path(raw_path)
        if not path.is_absolute():
            path = cwd / path
        if tool_name == "edit":
            return "changes", edit_diff(path, params)
        if tool_name == "write":
            return write_diff(path, params)
    except Exception:  # noqa: BLE001 - a preview must never break the gate
        _log.debug("permissions: could not build a preview", exc_info=True)
    return "changes", []
