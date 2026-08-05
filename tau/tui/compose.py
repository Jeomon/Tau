"""Composite an overlay's lines onto base lines, for the string renderer.

Width handling — wide glyphs occupying two columns, multi-codepoint grapheme
clusters, ANSI state carried across a splice — lives in ``ansi_text``, which
works on styled grapheme tokens rather than raw codepoints. Getting that wrong
is very visible (a misaligned box border, a popup bleeding into the text
behind it), and ``utils.wrap``-style per-codepoint slicing would misplace any
line containing a ZWJ emoji.
"""

from __future__ import annotations

from tau.tui.ansi_text import splice_ansi, wrap_ansi
from tau.tui.layout import Alignment
from tau.tui.style import Style, apply_style
from tau.tui.text import Line
from tau.tui.utils import strip_ansi, truncate_to_width, visible_width


def line_to_ansi_row(line: Line, width: int, row_style: Style | None = None) -> str:
    """``line_to_ansi`` plus an optional style patched across the whole row.

    Reproduces what ``Buffer.set_style(Rect(x, y, width, 1), style)`` did after
    a row was written: the style lands on every column including the trailing
    blanks, which is what makes a selected row read as a solid bar rather than
    a highlighted word. Patch direction matches ``Cell.set_style`` --
    ``existing.patch(row_style)`` -- so the row style wins where it sets a
    field.
    """
    if row_style is None:
        return line_to_ansi(line, width)

    out: list[str] = []
    col = 0
    for span in line:
        if col >= width:
            break
        clipped = truncate_to_width(span.content, width - col)
        if not clipped:
            continue
        style = line.style.patch(span.style).patch(row_style)
        out.append(apply_style(style, clipped))
        col += visible_width(clipped)
    if col < width:
        out.append(apply_style(row_style, " " * (width - col)))
    return "".join(out)


def composite_line(
    base: str,
    overlay: str,
    col: int,
    overlay_width: int,
    total_width: int,
) -> str:
    """Return ``base`` with ``overlay`` painted over it starting at column ``col``.

    Delegates to ``ansi_text.splice_ansi``, which tracks column occupancy so a
    double-width glyph survives having its *continuation* column overwritten:
    the glyph still prints and the overlay's first column is swallowed. Getting
    that wrong is the entire difficulty here — a naive string splice diverges
    on wide glyphs.

    Columns outside ``[0, total_width)`` are dropped rather than wrapped.
    """
    return splice_ansi(base, overlay, col, overlay_width, total_width)


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


def line_to_ansi(line: Line, width: int, x: int = 0) -> str:
    """Flatten a ``Line`` of styled spans into one ANSI string.

    The string-contract counterpart of ``Buffer.set_line``, for components that
    build structured ``Line``/``Span`` content — selectors, pickers, the
    spinner, footer widgets.

    Pure string assembly: no ``Buffer`` is built. Spans are clipped to the
    remaining columns with ``truncate_to_width``, which will not split a
    grapheme cluster, and alignment is resolved by padding the way
    ``set_line`` resolves it.
    """
    if width <= 0:
        return ""

    content_width = line.width
    start = x
    if line.alignment is Alignment.CENTER:
        start = x + max(0, (width - content_width) // 2)
    elif line.alignment is Alignment.RIGHT:
        start = x + max(0, width - content_width)

    out: list[str] = []
    if start:
        out.append(" " * start)
    col = start
    limit = x + width
    for span in line:
        if col >= limit:
            break
        text = truncate_to_width(span.content, limit - col)
        if not text:
            continue
        # Mirrors Buffer.set_line: the line's base style sits behind the span's
        # own, so a Line carrying a style is not silently dropped.
        out.append(apply_style(line.style.patch(span.style), text))
        col += visible_width(text)
    return "".join(out)


_PRINTABLE_ASCII = frozenset(chr(c) for c in range(0x20, 0x7F))


def wrap_to_rows(line: str, width: int) -> list[str]:
    """Split one styled line into terminal rows.

    Fast path: a line that is printable ASCII and already fits is provably a
    single row of one-column glyphs. ASCII cannot contain a combining mark,
    ZWJ, variation selector or regional indicator (all are well above U+007F),
    and every printable ASCII character is exactly one column — so no
    measurement or segmentation is needed. This covers ~99.8% of tool output
    and ~71% of rendered markdown.

    Everything else goes through ``ansi_text.wrap_ansi``, which segments into
    grapheme clusters and measures their display width. Slower, but correct for
    wide glyphs and multi-codepoint clusters, where the string-level
    ``utils.wrap`` still measures per codepoint and would break a ZWJ emoji in
    the wrong place.
    """
    if line.isascii():
        visible = strip_ansi(line) if "\x1b" in line else line
        if len(visible) <= width and _PRINTABLE_ASCII.issuperset(visible):
            return [line]

    return wrap_ansi(line, width)
