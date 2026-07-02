from __future__ import annotations

import re

from .blocks import Block

# Tools whose calls/results carry no durable semantic value in a summary.
# Includes both PascalCase and tau's lowercase builtin names.
_NOISE_TOOLS = {
    "TodoWrite",
    "TodoRead",
    "ToolSearch",
    "WebSearch",
    "AskUser",
    "ask_user",
    "ExitSpecMode",
    "GenerateDroid",
}

_NOISE_STRINGS = [
    "Continue from where you left off.",
    "No response requested.",
    "IMPORTANT: TodoWrite was not called yet.",
]

_XML_WRAPPER_RE = re.compile(
    r"<(system-reminder|ide_opened_file|command-message|context-window-usage)[^>]*>"
    r".*?</\1>",
    re.DOTALL,
)


def _is_noise_user_block(text: str) -> bool:
    trimmed = text.strip()
    if any(s in trimmed for s in _NOISE_STRINGS):
        return True
    stripped = _XML_WRAPPER_RE.sub("", trimmed).strip()
    return len(stripped) == 0


def _clean_user_text(text: str) -> str:
    return _XML_WRAPPER_RE.sub("", text).strip()


def filter_noise(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for b in blocks:
        if b.kind == "tool_call" and b.name in _NOISE_TOOLS:
            continue
        if b.kind == "tool_result" and b.name in _NOISE_TOOLS:
            continue
        if b.kind == "user":
            if _is_noise_user_block(b.text):
                continue
            cleaned = _clean_user_text(b.text)
            if not cleaned:
                continue
            out.append(Block(kind="user", text=cleaned, source_index=b.source_index))
            continue
        out.append(b)
    return out
