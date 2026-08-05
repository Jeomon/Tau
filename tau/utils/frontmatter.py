"""Frontmatter parsing for the markdown files tau loads at runtime.

Skills and prompt templates share one on-disk shape — an optional ``---``
delimited header of ``key: value`` lines followed by the body — so they share
one parser rather than keeping a copy each.

This is deliberately not YAML: the header is flat, values are taken verbatim
up to the end of the line, and a malformed header degrades to "no metadata,
all body" instead of raising. Adding a YAML dependency for two-key headers
would cost import time on the startup path for nothing.
"""

from __future__ import annotations


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``text`` into its frontmatter mapping and the remaining body.

    Keys are lower-cased and stripped; the first ``:`` on a line separates key
    from value, so values may contain colons. Text without a leading ``---``,
    or with an unterminated header, comes back as ``({}, text)``.
    """
    text = text.lstrip("\n")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    return meta, body
