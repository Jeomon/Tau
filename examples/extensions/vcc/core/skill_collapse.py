from __future__ import annotations

import re

_SKILL_TAG_RE = re.compile(r'^-?\s*<skill\s+name="([^"]+)"')
_SKILL_CLOSE_RE = re.compile(r"^-?\s*</skill>")


def collapse_skill_lines(lines: list[str]) -> list[str]:
    """Collapse ``<skill name="X">...</skill>`` blocks in a list of lines.

    Deduplicates by skill name and drops all content inside the block.
    """
    result: list[str] = []
    seen: set[str] = set()
    inside = False

    for line in lines:
        m = _SKILL_TAG_RE.match(line)
        if m:
            inside = True
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                result.append(f"[skill: {name}]")
            continue
        if inside:
            if _SKILL_CLOSE_RE.match(line):
                inside = False
            continue
        result.append(line)
    return result


_SKILL_BLOCK_RE = re.compile(r'<skill\s+name="([^"]+)"[^>]*>.*?(?:</skill>|$)', re.DOTALL)


def collapse_skill_text(text: str) -> str:
    """Collapse ``<skill name="X" ...>...</skill>`` blocks in raw text."""
    return _SKILL_BLOCK_RE.sub(lambda m: f"[skill: {m.group(1)}]", text)
