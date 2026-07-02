from __future__ import annotations

import re

from .sections import SectionData

_BRIEF_MAX_LINES = 120
_TUI_SAFE_LINE_CHARS = 120

# Stable, single-line prefix used to locate the note even after line-wrapping
# (wrap_long_lines may insert a newline inside the full note text).
RECALL_MARKER = "Use `vcc_recall`"

RECALL_NOTE = (
    f"{RECALL_MARKER} to search for prior work, decisions, and context from before "
    "this summary. Do not redo work already completed."
)

_INDENT_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)?")
_HEADER_RE = re.compile(r"^\[.+\]")


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    body = "\n".join(f"- {i}" for i in items)
    return f"[{title}]\n{body}"


def _wrap_line(line: str, max_chars: int) -> list[str]:
    if len(line) <= max_chars:
        return [line]
    m = _INDENT_RE.match(line)
    indent = m.group(0) if m else ""
    continuation_indent = " " * min(len(indent), 8) if indent else ""
    wrapped: list[str] = []
    remaining = line
    prefix = ""
    while len(prefix) + len(remaining) > max_chars:
        available = max(20, max_chars - len(prefix))
        split_at = remaining.rfind(" ", 0, available)
        if split_at < available * 0.5:
            split_at = available
        wrapped.append(prefix + remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
        prefix = continuation_indent
    if remaining:
        wrapped.append(prefix + remaining)
    return wrapped


def wrap_long_lines(text: str, max_chars: int = _TUI_SAFE_LINE_CHARS) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        out.extend(_wrap_line(line, max_chars))
    return "\n".join(out)


def cap_brief(text: str) -> str:
    lines = text.split("\n")
    if len(lines) <= _BRIEF_MAX_LINES:
        return text
    omitted = len(lines) - _BRIEF_MAX_LINES
    kept = lines[-_BRIEF_MAX_LINES:]
    # Avoid cutting mid-section: start at the first section header.
    first_header = next((i for i, ln in enumerate(kept) if _HEADER_RE.match(ln)), -1)
    clean = kept[first_header:] if first_header > 0 else kept
    return f"...({omitted} earlier lines omitted)\n\n" + "\n".join(clean)


def format_summary(data: SectionData) -> str:
    header_parts = [
        p
        for p in (
            _section("Session Goal", data.session_goal),
            _section("Files And Changes", data.files_and_changes),
            _section("Commits", data.commits),
            _section("Outstanding Context", data.outstanding_context),
            _section("User Preferences", data.user_preferences),
        )
        if p
    ]

    parts: list[str] = []
    if header_parts:
        parts.append("\n\n".join(header_parts))
    if data.brief_transcript:
        parts.append(cap_brief(data.brief_transcript))

    if not parts:
        return ""

    # NOTE: RECALL_NOTE is appended once by compile_summary() after merge, not here.
    return wrap_long_lines("\n\n---\n\n".join(parts))
