from __future__ import annotations

import re
from typing import Any

from .build_sections import build_sections
from .filter_noise import filter_noise
from .format import RECALL_MARKER, RECALL_NOTE, cap_brief, format_summary, wrap_long_lines
from .normalize import normalize

_HEADER_NAMES = [
    "Session Goal",
    "Files And Changes",
    "Commits",
    "Outstanding Context",
    "User Preferences",
]

_SEPARATOR = "\n\n---\n\n"


def _section_of(text: str, header: str) -> str:
    """Extract a named ``[header]`` section from summary text."""
    tag = f"[{header}]"
    start = text.find(tag)
    if start < 0:
        return ""
    after = text[start:]
    candidates: list[int] = []
    for h in _HEADER_NAMES:
        if h == header:
            continue
        idx = after.find(f"[{h}]")
        if idx > 0:
            candidates.append(idx)
    sep = after.find("\n\n---\n\n")
    if sep > 0:
        candidates.append(sep)
    candidates.sort()
    end = candidates[0] if candidates else None
    return (after[:end] if end else after).strip()


def _brief_of(text: str) -> str:
    idx = text.find(_SEPARATOR)
    if idx < 0:
        return ""
    return text[idx + len(_SEPARATOR):].strip()


def _merge_file_lines(prev: str, fresh: str) -> str:
    categories = ("Modified", "Created", "Read")
    merged: dict[str, list[str]] = {c: [] for c in categories}
    seen: dict[str, set[str]] = {c: set() for c in categories}

    for text in (prev, fresh):
        for line in text.split("\n"):
            for cat in categories:
                prefix = f"- {cat}: "
                if not line.startswith(prefix):
                    continue
                rest = line[len(prefix):]
                rest = re.sub(r"\s*\(\+\d+ more\)\s*$", "", rest)
                for p in rest.split(","):
                    trimmed = p.strip()
                    if trimmed and trimmed not in seen[cat]:
                        seen[cat].add(trimmed)
                        merged[cat].append(trimmed)

    # Dedup: if already in Modified, drop from Created (file existed before).
    merged["Created"] = [p for p in merged["Created"] if p not in seen["Modified"]]

    def cap(items: list[str], limit: int) -> str:
        if len(items) <= limit:
            return ", ".join(items)
        return ", ".join(items[:limit]) + f" (+{len(items) - limit} more)"

    lines: list[str] = []
    if merged["Modified"]:
        lines.append(f"- Modified: {cap(merged['Modified'], 10)}")
    if merged["Created"]:
        lines.append(f"- Created: {cap(merged['Created'], 10)}")
    if merged["Read"]:
        lines.append(f"- Read: {cap(merged['Read'], 10)}")
    if not lines:
        return ""
    return "[Files And Changes]\n" + "\n".join(lines)


def _merge_header_section(header: str, prev: str, fresh: str) -> str:
    # Outstanding Context is volatile -- always use fresh only.
    if header == "Outstanding Context":
        return fresh
    if not prev:
        return fresh
    if not fresh:
        return prev

    if header == "Files And Changes":
        return _merge_file_lines(prev, fresh)

    def is_clean(ln: str) -> bool:
        return ln.startswith("- ") and "<skill" not in ln and "</skill" not in ln

    prev_lines = [ln for ln in prev.split("\n") if is_clean(ln)]
    fresh_lines = [ln for ln in fresh.split("\n") if is_clean(ln)]
    combined: list[str] = []
    seen: set[str] = set()
    for ln in [*prev_lines, *fresh_lines]:
        if ln not in seen:
            seen.add(ln)
            combined.append(ln)
    cap = 8 if header in ("Session Goal", "Commits") else 15
    capped = combined[-cap:] if len(combined) > cap else combined
    if not capped:
        return ""
    return f"[{header}]\n" + "\n".join(capped)


def _merge_brief_transcript(prev: str, fresh: str) -> str:
    if not prev:
        return fresh
    if not fresh:
        return prev
    return prev + "\n\n" + fresh


def _merge_previous(prev: str, fresh: str) -> str:
    headers = [
        merged
        for header in _HEADER_NAMES
        if (
            merged := _merge_header_section(
                header, _section_of(prev, header), _section_of(fresh, header)
            )
        )
    ]

    merged_brief = _merge_brief_transcript(_brief_of(prev), _brief_of(fresh))

    parts: list[str] = []
    if headers:
        parts.append("\n\n".join(headers))
    if merged_brief:
        parts.append(cap_brief(merged_brief))
    return _SEPARATOR.join(parts)


def _strip_recall_note(text: str) -> str:
    # Match on the stable marker prefix — the full note may have been wrapped
    # across a newline by wrap_long_lines, so an exact-string match can miss.
    idx = text.rfind(RECALL_MARKER)
    if idx < 0:
        return text
    head = text[:idx]
    return re.sub(r"\s*(?:\n\n---\n\n)?\s*$", "", head).rstrip()


def compile_summary(
    messages: list[Any],
    previous_summary: str | None = None,
) -> str:
    """Produce a bracketed-section + brief-transcript summary from messages.

    ``messages`` is a list of tau ``AgentMessage`` objects. No LLM is used.
    """
    blocks = filter_noise(normalize(messages))
    data = build_sections(blocks)
    fresh = format_summary(data)
    prev = _strip_recall_note(previous_summary) if previous_summary else None
    merged = _merge_previous(prev, fresh) if prev else fresh
    if not merged:
        return ""
    return wrap_long_lines(merged + _SEPARATOR + RECALL_NOTE)
