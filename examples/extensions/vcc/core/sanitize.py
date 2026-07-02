from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize(text: str) -> str:
    """Normalize newlines and strip ANSI escapes / control characters."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_RE.sub("", text)
    return _CTRL_RE.sub("", text)
