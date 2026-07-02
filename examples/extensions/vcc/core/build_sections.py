from __future__ import annotations

import re

from .blocks import Block
from .brief import build_brief_sections, stringify_brief
from .content import clip_sentence, non_empty_lines
from .extract.commits import extract_commits, format_commits
from .extract.files import extract_files
from .extract.goals import extract_goals
from .extract.preferences import dedup_preferences_against_goals, extract_preferences
from .sections import SectionData

_BLOCKER_RE = re.compile(
    r"\b(fail(ed|s|ure|ing)?|broken|cannot|can't|won't work|does not work|doesn't work|"
    r"still (broken|failing|wrong)|blocked|blocker|not (fixed|resolved|working)|"
    r"crash(es|ed|ing)?)\b",
    re.I,
)
_BULLET_RE = re.compile(r"^\s*[-*+>]\s")
_PAREN_RE = re.compile(r"^\s*\(")
_SENTENCE_START_RE = re.compile(r"^\s*[\"'`*_]?[A-Z`]")


def _extract_outstanding_context(blocks: list[Block]) -> list[str]:
    items: list[str] = []
    for b in blocks[-20:]:
        if b.kind not in ("assistant", "user"):
            continue
        for line in non_empty_lines(b.text):
            if not _BLOCKER_RE.search(line):
                continue
            if len(line) < 15:
                continue
            if _BULLET_RE.match(line) or _PAREN_RE.match(line):
                continue
            if not _SENTENCE_START_RE.match(line):
                continue
            clipped = (
                f"[user] {clip_sentence(line, 150)}"
                if b.kind == "user"
                else clip_sentence(line, 150)
            )
            if clipped not in items:
                items.append(clipped)
            break
    return items[:5]


def _cap(paths: set[str], limit: int) -> str:
    arr = list(paths)
    if len(arr) <= limit:
        return ", ".join(arr)
    return ", ".join(arr[:limit]) + f" (+{len(arr) - limit} more)"


def _format_file_activity(blocks: list[Block]) -> list[str]:
    act = extract_files(blocks)
    # Dedup: if already Modified, drop from Created (file existed before).
    act.created -= act.modified
    lines: list[str] = []
    if act.modified:
        lines.append(f"Modified: {_cap(act.modified, 10)}")
    if act.created:
        lines.append(f"Created: {_cap(act.created, 10)}")
    if act.read:
        lines.append(f"Read: {_cap(act.read, 10)}")
    return lines


def build_sections(blocks: list[Block]) -> SectionData:
    brief_sections = build_brief_sections(blocks)
    session_goal = extract_goals(blocks)
    user_preferences = dedup_preferences_against_goals(
        extract_preferences(blocks), session_goal
    )
    return SectionData(
        session_goal=session_goal,
        outstanding_context=_extract_outstanding_context(blocks),
        files_and_changes=_format_file_activity(blocks),
        commits=format_commits(extract_commits(blocks)),
        user_preferences=user_preferences,
        brief_transcript=stringify_brief(brief_sections),
    )
