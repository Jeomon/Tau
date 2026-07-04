"""Test helpers for the Buffer/Widget render layer.

Pairs with ``TestBackend`` (``backend.py``): render into a real ``Buffer``
via ``TestBackend``/``BufferedTerminal``, then assert on it directly instead
of scraping ANSI strings.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer


def assert_buffer_eq(actual: Buffer, expected: Buffer) -> None:
    """Assert two buffers are identical, raising with a readable per-cell diff if not.

    Mirrors ratatui's ``assert_buffer_eq!`` — the failure message shows both
    buffers' text content plus the exact ``(x, y)`` cells that differ,
    instead of a single opaque ``!=``.
    """
    if actual.area != expected.area:
        raise AssertionError(f"buffer area mismatch: {actual.area!r} != {expected.area!r}")

    mismatches: list[str] = []
    w = actual.area.width
    for i, (a, e) in enumerate(zip(actual.content, expected.content, strict=True)):
        if a != e:
            x, y = actual.area.x + i % w, actual.area.y + i // w
            mismatches.append(f"  ({x}, {y}): actual={a!r} expected={e!r}")

    if not mismatches:
        return

    def render(buf: Buffer) -> str:
        rows = []
        for y in range(buf.area.top, buf.area.bottom):
            cols = range(buf.area.left, buf.area.right)
            rows.append("".join(buf.get(x, y).symbol or " " for x in cols))
        return "\n".join(rows)

    raise AssertionError(
        "buffers differ:\n"
        f"--- actual ---\n{render(actual)}\n"
        f"--- expected ---\n{render(expected)}\n"
        "--- mismatched cells ---\n" + "\n".join(mismatches)
    )
