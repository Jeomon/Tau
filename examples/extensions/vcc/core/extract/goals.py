from __future__ import annotations

import re

from ..blocks import Block
from ..content import clip, non_empty_lines
from ..skill_collapse import collapse_skill_lines

_SCOPE_CHANGE_RE = re.compile(
    r"\b(instead|actually|change of plan|forget that|new task|switch to|"
    r"now I want|pivot|let'?s do|stop .* and)\b",
    re.I,
)

_TASK_RE = re.compile(
    r"\b(fix|implement|add|create|build|refactor|debug|investigate|update|"
    r"remove|delete|migrate|deploy|test|write|set up)\b",
    re.I,
)

_NOISE_SHORT_RE = re.compile(
    r"^(ok|yes|no|sure|yeah|yep|go|hi|hey|thx|thanks|ok\b.*|y|n|k)\s*[.!?]*$", re.I
)

# Reject lines that are clearly not user goals (pasted output, code, paths, tool
# dumps) or meta-prompt boilerplate.
_NON_GOAL_RE = re.compile(
    r"^\s*[\[│├└─╭╰]|```|^\s*(=[A-Z]+\(|function |const |let |var |import |export |class )|"
    r"^(https?:|file:|/[A-Za-z])|\\n|^\s*For each\b|"
    r"\bin full\b[^\n]*\b(comments|issue|issues|PRs?|linked)\b"
)

_TEMPLATE_SIGNAL_RE = re.compile(
    r"^\s*(For each\b|Do NOT implement\b|Analyze and propose\b|If Task/context\b|Output:\s*$)",
    re.I,
)

_MAX_GOAL_CHARS = 200
_LEADING_CHARS = 200


def _truncate_at_template(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if _TEMPLATE_SIGNAL_RE.match(line):
            return lines[:i]
    return lines


def _strip_leading_bullet(line: str) -> str:
    return re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line).strip()


def _is_substantive_goal(text: str) -> bool:
    t = text.strip()
    if len(t) <= 5:
        return False
    if len(t) > _MAX_GOAL_CHARS:
        return False
    if _NOISE_SHORT_RE.match(t):
        return False
    return not _NON_GOAL_RE.search(t)


def extract_goals(blocks: list[Block]) -> list[str]:
    goals: list[str] = []
    latest_scope_change: list[str] | None = None

    for b in blocks:
        if b.kind != "user":
            continue
        raw_lines = non_empty_lines(b.text)
        truncated = _truncate_at_template(raw_lines)
        lines = [
            stripped
            for line in collapse_skill_lines([ln for ln in truncated if _is_substantive_goal(ln)])
            if len(stripped := _strip_leading_bullet(line)) > 5
        ]
        if not lines:
            continue

        if not goals:
            goals.extend(lines[:6])
            continue

        leading = b.text[:_LEADING_CHARS]
        if _SCOPE_CHANGE_RE.search(leading):
            latest_scope_change = [clip(ln, _MAX_GOAL_CHARS) for ln in lines[:3]]
        elif _TASK_RE.search(leading) and len(lines[0]) > 15:
            latest_scope_change = [clip(ln, _MAX_GOAL_CHARS) for ln in lines[:2]]

    if latest_scope_change:
        goals.append("[Scope change]")
        goals.extend(latest_scope_change)

    return goals[:8]
