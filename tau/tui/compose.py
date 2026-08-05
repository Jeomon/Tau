"""Composite an overlay's lines onto base lines, for the string renderer.

The transcript moved to strings because it is huge and the cell round trip
dominated it. Overlays are the opposite: bounded by the overlay's own size —
tens of rows, redrawn only while one is open — so the round trip costs nothing
measurable here, while getting column arithmetic wrong is very visible (a
misaligned box border, a popup bleeding into the text behind it).

So compositing deliberately still goes through cells. That reuses the exact
width handling the cell renderer already had — wide glyphs occupying two
columns, multi-codepoint grapheme clusters, ANSI state carried across the
splice — rather than reimplementing a width-aware string slicer with its own
bug surface. ``utils.wrap``-style per-codepoint slicing would misplace any
line containing a ZWJ emoji.
"""

from __future__ import annotations

from tau.tui.ansi_bridge import parse_ansi_into, parse_ansi_wrapped_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.utils import strip_ansi


def composite_line(
    base: str,
    overlay: str,
    col: int,
    overlay_width: int,
    total_width: int,
) -> str:
    """Return ``base`` with ``overlay`` painted over it starting at column ``col``.

    Columns outside ``[0, total_width)`` are dropped, matching the cell
    renderer's blit, which skipped out-of-range target columns rather than
    wrapping them onto another row.
    """
    if total_width <= 0 or overlay_width <= 0:
        return base

    buf = Buffer.empty(Rect(0, 0, total_width, 1))
    parse_ansi_into(buf, 0, 0, base, total_width)

    ov = Buffer.empty(Rect(0, 0, overlay_width, 1))
    parse_ansi_into(ov, 0, 0, overlay, overlay_width)

    for x in range(overlay_width):
        target = col + x
        if 0 <= target < total_width:
            # Replace the reference rather than mutating through Buffer.set:
            # base rows may share Cell objects with a frozen cache, and an
            # in-place write would bake overlay pixels into it permanently.
            # ``ov`` is private to this call, so sharing its cells is safe.
            buf.content[target] = ov.content[x]

    return row_to_ansi(buf, 0, embed_raw=True, trim_trailing_blanks=True)


def composite_lines(
    base_lines: list[str],
    overlay_lines: list[str],
    row: int,
    col: int,
    overlay_width: int,
    total_width: int,
) -> list[str]:
    """Paint ``overlay_lines`` onto ``base_lines`` at (``row``, ``col``).

    ``base_lines`` is extended with blanks if the overlay reaches past the end,
    mirroring the cell path's ``buf.grow_to`` before blitting. Rows above the
    start of the base (negative targets) are dropped.
    """
    out = list(base_lines)
    needed = row + len(overlay_lines)
    if needed > len(out):
        out.extend([""] * (needed - len(out)))

    for i, ov_line in enumerate(overlay_lines):
        target = row + i
        if target < 0:
            continue
        out[target] = composite_line(out[target], ov_line, col, overlay_width, total_width)
    return out


_PRINTABLE_ASCII = frozenset(chr(c) for c in range(0x20, 0x7F))


def wrap_to_rows(line: str, width: int) -> list[str]:
    """Split one styled line into terminal rows, exactly as the cell path would.

    Fast path: a line that is printable ASCII and already fits is provably a
    single row of one-column cells. ASCII cannot contain a combining mark, ZWJ,
    variation selector or regional indicator (all are well above U+007F), and
    every printable ASCII character is exactly one column — so no measurement
    or segmentation is needed. This covers ~99.8% of tool output and ~71% of
    rendered markdown.

    Everything else goes through ``parse_ansi_wrapped_into`` and back, which is
    the existing, exact wrapping. Slower, but it is the same code the cell
    renderer used, so the two paths can never disagree — notably for wide
    glyphs and multi-codepoint clusters, where the string-level ``utils.wrap``
    still measures per codepoint and would break a ZWJ emoji in the wrong place.
    """
    if line.isascii():
        visible = strip_ansi(line) if "\x1b" in line else line
        if len(visible) <= width and _PRINTABLE_ASCII.issuperset(visible):
            return [line]

    buf = Buffer.empty(Rect(0, 0, max(1, width), 0))
    rows = parse_ansi_wrapped_into(buf, 0, 0, line, width)
    return [row_to_ansi(buf, y, embed_raw=True, trim_trailing_blanks=True) for y in range(rows)]
