"""Painting a fixed region at the bottom of the *main* screen.

This is how an app-owned viewport can exist without the alternate screen: claim
the bottom ``height`` rows once, then repaint them in place forever after. fzf's
``--height`` mode works the same way.

The whole technique rests on one rule: **never emit a line feed on the region's
last row.** A line feed there scrolls the screen, which pushes the top row of
the region into scrollback and shifts everything up by one — the region drifts
and the terminal accumulates junk. Every function here is written to that
constraint, and the tests assert it directly rather than trusting the code.

Deliberately free of any I/O: these build strings, so the sequences can be
asserted on in tests instead of being inferred from terminal behaviour.
"""

from __future__ import annotations

from tau.tui.utils import clip_to_width


def reserve(height: int) -> str:
    """Claim ``height`` rows at the bottom of the screen, cursor left at the top.

    Emitted once, when the app-viewport backend starts. The line feeds scroll
    existing content up to make room — the one moment this backend is *allowed*
    to scroll, because it is creating the region rather than drawing in it.
    Afterwards the cursor sits on the region's first row, which is the position
    :func:`paint` both expects and restores.
    """
    if height <= 1:
        return "\r"
    return "\r\n" * (height - 1) + f"\x1b[{height - 1}A" + "\r"


def paint(lines: list[str], height: int, width: int) -> str:
    """Repaint the region in place from the cursor's current row.

    Assumes the cursor is on the region's first row (where :func:`reserve` and
    every previous ``paint`` leave it) and returns it there, so repainting is
    idempotent and needs no absolute addressing — meaning the caller never has
    to know where on screen the region sits.

    Rows beyond ``lines`` are cleared rather than skipped, so a shrinking
    transcript cannot leave stale text behind. Content is clipped to ``width``
    because a line that overflows would soft-wrap, silently adding a row and
    pushing the region out of alignment.
    """
    height = max(0, height)
    if height == 0:
        return ""
    width = max(1, width)

    out: list[str] = []
    for row in range(height):
        if row > 0:
            # Safe: this advances *into* the region, never off its last row.
            out.append("\r\n")
        out.append("\x1b[2K")  # clear the row before drawing
        if row < len(lines):
            out.append(clip_to_width(lines[row], width))

    # Back to the top of the region. No trailing line feed — see module docs.
    if height > 1:
        out.append(f"\x1b[{height - 1}A")
    out.append("\r")
    return "".join(out)


def release(height: int) -> str:
    """Leave the region behind on exit, cursor below it.

    The counterpart to the alternate screen's restore, except nothing is wiped:
    the final frame stays on the normal screen as ordinary scrollback, which is
    the one advantage this model keeps over alt-screen.
    """
    height = max(0, height)
    if height == 0:
        return "\r"
    return f"\x1b[{height - 1}B\r\n" if height > 1 else "\r\n"
