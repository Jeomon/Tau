"""Mermaid diagrams rendered as Unicode box art for the terminal.

``termaid`` parses Mermaid source directly and lays it out with box-drawing
characters, so a diagram shows up in every terminal rather than only the ones
that speak an image protocol — and it does it in single-digit milliseconds,
fast enough to run inline in a render pass instead of behind a worker.

Every failure path falls back to the caller's normal fenced-code rendering, so
an unsupported or malformed diagram degrades to the source text it would have
shown anyway.
"""

from __future__ import annotations

from functools import lru_cache

#: Fence languages that mean "this is a Mermaid diagram".
MERMAID_LANGUAGES = frozenset({"mermaid", "mmd"})


def is_mermaid_language(lang: str) -> bool:
    return lang.strip().lower() in MERMAID_LANGUAGES


@lru_cache(maxsize=256)
def _render_cached(source: str) -> tuple[str, ...] | None:
    """Lay ``source`` out as box art, or None when it cannot be rendered.

    Cached because a scrollback repaint re-renders the same diagram on every
    frame, and because the layout depends only on the source text.
    """
    try:
        import termaid
    except ImportError:  # pragma: no cover - termaid is a hard dependency
        return None

    try:
        art = termaid.render(source)
    except Exception:
        # termaid returns "" for input it cannot parse rather than raising, but
        # a diagram type it half-understands could still trip an internal error;
        # neither case should take the whole message rendering down with it.
        return None

    if not art or not art.strip():
        return None
    return tuple(art.rstrip("\n").split("\n"))


def render_diagram(source: str, width: int) -> list[str] | None:
    """Box art for ``source`` that fits ``width`` columns, else None.

    ``termaid`` lays out to whatever width the diagram needs — there is no
    parameter to constrain it — so a wide graph would wrap into noise in a
    narrow terminal. Returning None in that case hands the caller back to the
    source text, which at least wraps legibly.
    """
    if width <= 0:
        return None
    lines = _render_cached(source)
    if lines is None:
        return None
    if max((len(line) for line in lines), default=0) > width:
        return None
    return list(lines)
