from __future__ import annotations

import re


def clip(text: str, max_len: int = 200) -> str:
    """Clip ``text`` to ``max_len`` chars, preferring a trailing word boundary."""
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len)
    end = cut if cut > max_len * 0.6 else max_len
    return text[:end]


_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def clip_sentence(text: str, max_len: int = 200) -> str:
    """Clip to the last sentence boundary at or before ``max_len`` chars.

    Falls back to a word boundary (:func:`clip`) when no sentence end is found
    in the acceptable ``[max_len*0.5, max_len]`` window.
    """
    if len(text) <= max_len:
        return text
    window = text[:max_len]
    matches = list(_SENTENCE_END_RE.finditer(window))
    if matches:
        last = matches[-1]
        end = last.start() + 1  # include the punctuation
        if end >= max_len * 0.5:
            return text[:end]
    return clip(text, max_len)


def non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]
