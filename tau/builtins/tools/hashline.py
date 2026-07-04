"""Content-based per-line anchor hashes shared by the read and edit tools.

Both tools must compute the exact same hash for the exact same line, since
``edit`` re-derives anchors from a fresh read of the file rather than trusting
any state carried over from a prior ``read`` call. Keeping the algorithm in
one place is what keeps them in agreement.
"""

from __future__ import annotations

import hashlib

HASH_LEN = 4
# Astronomically unlikely to matter for any real file (65536 buckets per
# retry round), but bounds the loop instead of spinning forever in a
# pathological worst case (e.g. a file that is mostly one repeated line,
# longer than the entire hash space).
_MAX_RETRIES = 4096


def _base_hash(content: str, retry: int) -> str:
    basis = content if retry == 0 else f"{content}\x00{retry}"
    return hashlib.md5(basis.encode()).hexdigest()[:HASH_LEN]


def compute_line_hashes(lines: list[str]) -> list[str]:
    """Return one anchor hash per line, unique within this file (perfect hashing).

    The base hash is ``md5(stripped content)[:4]`` — identical to a plain
    per-line hash for the common case of non-repeated content, so most lines
    get the same anchor a naive per-line hash would produce. When a line's
    base hash collides with one already assigned to an earlier line in this
    file, the hash is recomputed with an increasing retry suffix until a free
    slot is found, so every line — including blank lines and repeated
    boilerplate like ``}`` or ``import os`` — gets its own distinct anchor.
    This removes any need to break ties by line-number proximity when
    resolving an anchor back to a line.
    """
    assigned: set[str] = set()
    hashes: list[str] = []
    for line in lines:
        content = line.strip()
        if not content:
            # Blank lines carry no content to hash meaningfully, but still
            # need a unique anchor like any other line — chain off a fixed
            # marker instead of the (also blank) stripped content.
            content = "\x00blank"
        retry = 0
        h = _base_hash(content, retry)
        while h in assigned and retry < _MAX_RETRIES:
            retry += 1
            h = _base_hash(content, retry)
        assigned.add(h)
        hashes.append(h)
    return hashes
