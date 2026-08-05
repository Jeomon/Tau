"""Grapheme-aware ANSI string manipulation: tokenize, wrap, splice.

This is the string-native replacement for what ``ansi_bridge``/``buffer`` did
via a ``Cell`` grid. The correctness property that mattered there and still
matters here is *cluster* handling: a combining accent, a ZWJ emoji sequence,
a variation selector or a regional-indicator flag is one glyph occupying one
(or two) columns, and must never be split. ``utils.wrap`` measures per
codepoint and gets all of those wrong; everything in this module works on
whole grapheme clusters instead.

The pipeline is one tokenizer plus two consumers:

- ``tokenize`` — an ANSI string -> ``[(cluster, width, style)]``, resolving
  SGR/OSC-8 escapes into a real ``Style`` as it scans.
- ``wrap_tokens`` / ``wrap_ansi`` — greedy width-aware line breaking over
  those tokens, with hanging-indent continuation.
- ``splice_ansi`` — overlay compositing.

``splice_ansi`` keeps a local column array because column *occupancy* is the
whole problem it solves: a double-width glyph owns two columns, and an
overlay landing on the second one is swallowed rather than splitting the
glyph in half. That array is a list of ``(symbol, style)`` tuples scoped to a
single function — not a revived cell grid. Compositing runs per overlay row,
tens of rows at a time and only while an overlay is open, so it is nowhere
near a hot path.
"""

from __future__ import annotations

import re

import grapheme

from tau.tui.style import (
    OSC8_CLOSE,
    Color,
    Modifier,
    Style,
    style_transition,
)
from tau.tui.utils import _ANSI_RE, grapheme_width

_RESET = "\x1b[0m"

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m$")
_LINK_RE = re.compile(r"\x1b\]8;;(.*?)(?:\x07|\x1b\\)$")

_SET_MODIFIER = {
    1: Modifier.BOLD,
    2: Modifier.DIM,
    3: Modifier.ITALIC,
    4: Modifier.UNDERLINE,
    5: Modifier.BLINK,
    7: Modifier.REVERSED,
    9: Modifier.STRIKETHROUGH,
}
_UNSET_MODIFIER = {
    21: Modifier.BOLD,
    22: Modifier.BOLD | Modifier.DIM,
    23: Modifier.ITALIC,
    24: Modifier.UNDERLINE,
    25: Modifier.BLINK,
    27: Modifier.REVERSED,
    29: Modifier.STRIKETHROUGH,
}

_BASE_NAMES = {
    30: "black",
    31: "red",
    32: "green",
    33: "yellow",
    34: "blue",
    35: "magenta",
    36: "cyan",
    37: "white",
    90: "bright_black",
    91: "bright_red",
    92: "bright_green",
    93: "bright_yellow",
    94: "bright_blue",
    95: "bright_magenta",
    96: "bright_cyan",
    97: "bright_white",
}


def _sgr_base_name(code: int) -> str:
    return _BASE_NAMES.get(code, "default")


# An inline-image escape is atomic: its payload is not printable text and must
# survive verbatim rather than being scanned as cells or eaten as an
# unrecognized no-op escape. Checked by substring, not prefix — the iTerm2 form
# leads with a relative cursor-up move before the OSC 1337 sequence.
def is_image_escape(line: str) -> bool:
    """True if ``line`` carries a Kitty or iTerm2 inline-image sequence."""
    return "\x1b_G" in line or "\x1b]1337;File=" in line


class SgrState:
    """Accumulates SGR/OSC-8 escapes into a live ``Style``, one code run at a time."""

    __slots__ = ("style",)

    def __init__(self) -> None:
        self.style = Style()

    def process(self, code: str) -> None:
        link_m = _LINK_RE.match(code)
        if link_m:
            self.style = Style(
                fg=self.style.fg,
                bg=self.style.bg,
                underline_color=self.style.underline_color,
                link=link_m.group(1) or None,
                add_modifier=self.style.add_modifier,
            )
            return
        m = _SGR_RE.match(code)
        if not m:
            return
        params = m.group(1)
        if not params:
            self.style = Style()
            return
        nums = [int(x) if x else 0 for x in params.split(";")]
        i = 0
        add = self.style.add_modifier
        fg, bg, ul = self.style.fg, self.style.bg, self.style.underline_color
        while i < len(nums):
            n = nums[i]
            if n == 0:
                add, fg, bg, ul = Modifier.NONE, None, None, None
            elif n in _SET_MODIFIER:
                add |= _SET_MODIFIER[n]
            elif n in _UNSET_MODIFIER:
                add &= ~_UNSET_MODIFIER[n]
            elif n == 39:
                fg = None
            elif n == 49:
                bg = None
            elif n == 59:
                ul = None
            elif 30 <= n <= 37 or 90 <= n <= 97:
                fg = _sgr_base_name(n)
            elif 40 <= n <= 47 or 100 <= n <= 107:
                bg = _sgr_base_name(n - 10)
            elif n in (38, 48, 58) and i + 1 < len(nums):
                target = nums[i + 1]
                if target == 5 and i + 2 < len(nums):
                    color: Color = nums[i + 2]
                    i += 2
                elif target == 2 and i + 4 < len(nums):
                    color = (nums[i + 2], nums[i + 3], nums[i + 4])
                    i += 4
                else:
                    i += 1
                    continue
                if n == 38:
                    fg = color
                elif n == 48:
                    bg = color
                else:
                    ul = color
            i += 1
        self.style = Style(fg=fg, bg=bg, underline_color=ul, link=self.style.link, add_modifier=add)


#: One rendered glyph: its cluster text, column width, and resolved style.
Token = tuple[str, int, Style]


def tokenize(line: str) -> list[Token]:
    """Split an ANSI-laden string into styled grapheme tokens.

    Escapes are consumed into the running ``Style`` and never emitted as
    tokens, so the result is pure content — one entry per visible glyph, with
    zero-width clusters dropped.
    """
    state = SgrState()
    tokens: list[Token] = []
    index = 0
    n = len(line)
    while index < n:
        ch = line[index]
        match = _ANSI_RE.match(line, index) if ch == "\x1b" else None
        if match:
            state.process(match.group(0))
            index += len(match.group(0))
            continue
        # Fast path for plain ASCII, which is the overwhelming bulk of a
        # transcript. Nothing that combines is ASCII — combining marks, ZWJ,
        # variation selectors and regional indicators are all well above
        # U+007F — so a printable-ASCII char followed by another printable
        # ASCII char (or by end-of-line) is always a standalone one-column
        # cluster needing no segmentation.
        #
        # Worth the special case because the general path slices ``line[index:]``
        # and builds a fresh grapheme iterator for *every* character — O(n^2)
        # bytes copied per line — which profiling showed to be ~89% of a resize
        # rewrite. Anything not covered here, including "\r\n" (a single cluster
        # under UAX #29) and any char next to a non-ASCII byte, still falls
        # through and is segmented properly.
        if " " <= ch <= "~":
            following = line[index + 1 : index + 2]
            if not following or " " <= following <= "~":
                tokens.append((ch, 1, state.style))
                index += 1
                continue
        cluster = next(iter(grapheme.graphemes(line[index:])), "")
        if not cluster:
            index += 1
            continue
        index += len(cluster)
        width = grapheme_width(cluster)
        if width:
            tokens.append((cluster, width, state.style))
    return tokens


def emit(tokens: list[Token], max_width: int | None = None) -> str:
    """Flatten styled tokens back into one ANSI string.

    Emits a style transition only where the style actually changes, then
    closes any open OSC-8 link and resets if a non-default style is still
    active — matching what the cell renderer emitted per row.

    ``max_width`` clips the row at the first glyph that would overrun it, and
    drops everything after. A glyph wider than the space left is dropped
    outright rather than being cut in half: a double-width cluster in a
    one-column row has no valid rendering, so the row comes out blank. That is
    what the cell writer did — it refused to place a glyph whose second column
    fell outside the row — and callers rely on it to keep column arithmetic
    exact.
    """
    out: list[str] = []
    active: Style | None = None
    column = 0
    for cluster, width, style in tokens:
        if max_width is not None and column + width > max_width:
            break
        if style != active:
            out.append(style_transition(active, style))
            active = style
        out.append(cluster or " ")
        column += width
    if active is not None:
        if active.link:
            out.append(OSC8_CLOSE)
        if active != Style():
            out.append(_RESET)
    return "".join(out)


def patch_row_style(line: str, width: int, style: Style) -> str:
    """Patch ``style`` behind every column of ``line``, padded out to ``width``.

    The string counterpart of painting a background over a whole row: the
    patch direction is ``existing.patch(style)``, so the row style wins only
    where it actually sets a field and the content keeps its own foreground
    and modifiers. Trailing columns are emitted as styled spaces, which is
    what makes a highlighted row read as a solid bar rather than a
    highlighted word.
    """
    patched: list[Token] = []
    column = 0
    for cluster, glyph_width, token_style in tokenize(line):
        if column + glyph_width > width:
            break
        patched.append((cluster, glyph_width, token_style.patch(style)))
        column += glyph_width
    tail = width - column
    if tail > 0:
        patched.append((" " * tail, tail, Style().patch(style)))
    return emit(patched)


def wrap_tokens(tokens: list[Token], max_width: int) -> list[list[Token]]:
    """Greedily break styled tokens into rows of at most ``max_width`` columns.

    Breaks at the last space that fits; falls back to a hard split when a
    single token cannot fit on its own row. Leading whitespace becomes a
    hanging indent repeated on every continuation row, unless that indent
    would consume the whole width.
    """
    if max_width <= 0:
        return []
    if not tokens:
        return [[]]

    indent_end = 0
    while indent_end < len(tokens) and tokens[indent_end][0].isspace():
        indent_end += 1
    indent = tokens[:indent_end]
    indent_width = sum(token[1] for token in indent)
    if indent_width >= max_width:
        indent = []
        indent_width = 0

    rows: list[list[Token]] = []
    remaining = tokens
    first = True
    while remaining:
        prefix: list[Token] = [] if first else indent
        capacity = max_width - (0 if first else indent_width)
        taken = 0
        used = 0
        last_space = 0
        while taken < len(remaining) and used + remaining[taken][1] <= capacity:
            used += remaining[taken][1]
            taken += 1
            if remaining[taken - 1][0].isspace():
                last_space = taken
        if taken < len(remaining) and last_space:
            taken = last_space
        if taken == 0:
            taken = 1
        rows.append([*prefix, *remaining[:taken]])
        remaining = remaining[taken:]
        first = False
    return rows


def wrap_ansi(line: str, max_width: int) -> list[str]:
    """Split one styled line into terminal rows of at most ``max_width`` columns.

    An inline-image line is atomic and always occupies exactly one row: the
    escape verbatim, followed by ``max_width`` spaces. The padding is load
    bearing — the escape draws pixels the terminal owns but consumes no
    columns, so the row still has to account for its full width or everything
    composited onto it afterwards lands in the wrong place.
    """
    if max_width <= 0:
        return []
    if is_image_escape(line):
        return [line + " " * max_width]
    return [emit(row, max_width) for row in wrap_tokens(tokenize(line), max_width)]


def splice_ansi(
    base: str,
    overlay: str,
    col: int,
    overlay_width: int,
    total_width: int,
) -> str:
    """Return ``base`` with ``overlay`` painted over it starting at column ``col``.

    Works on a local column array because column occupancy is the point: a
    double-width glyph in ``base`` owns two columns, and an overlay landing on
    its *continuation* column is swallowed whole rather than cutting the glyph
    in half. Columns outside ``[0, total_width)`` are dropped rather than
    wrapped.

    Trailing columns never written by either side are trimmed instead of being
    emitted as spaces — the caller has already erased the line, so those
    spaces would be pure padding. A written space is content and is kept, so a
    styled run reaching the edge (a code block's background) survives.
    """
    if total_width <= 0 or overlay_width <= 0:
        return base

    # None marks a column nothing has written yet, which is what makes the
    # trailing-blank trim safe: it can be told apart from a real space.
    columns: list[tuple[str, Style] | None] = [None] * total_width

    def paint(target: list[tuple[str, Style] | None], text: str, limit: int) -> str:
        """Write ``text``'s glyphs into ``target``; returns leading raw payload, if any."""
        if is_image_escape(text):
            # Atomic: contributes no columns, only a verbatim prefix.
            return text
        column = 0
        for cluster, width, style in tokenize(text):
            if column + width > limit:
                break
            target[column] = (cluster, style)
            if width == 2 and column + 1 < limit:
                # Continuation column. Mirrors the cell writer, which stored an
                # empty symbol coerced to a space.
                target[column + 1] = (" ", style)
            column += width
        return ""

    raw_prefix = paint(columns, base, total_width)

    overlay_columns: list[tuple[str, Style] | None] = [None] * overlay_width
    paint(overlay_columns, overlay, overlay_width)
    for x in range(overlay_width):
        target = col + x
        if 0 <= target < total_width:
            columns[target] = overlay_columns[x]

    # A raw payload on this row suppresses the trim entirely: the escape draws
    # pixels but consumes no columns, so the row must still account for its
    # full width or anything composited over it afterwards is misaligned. Only
    # the base can contribute one — an overlay's raw payload is dropped, since
    # compositing copies the overlay's columns and not its raw writes.
    end = total_width
    if not raw_prefix:
        while end > 0 and columns[end - 1] is None:
            end -= 1

    out: list[str] = [raw_prefix] if raw_prefix else []
    active: Style | None = None
    skip_columns = 0
    for cell in columns[:end]:
        symbol, style = (" ", Style()) if cell is None else cell
        if skip_columns > 0:
            skip_columns -= 1
            continue
        if style != active:
            out.append(style_transition(active, style))
            active = style
        out.append(symbol or " ")
        # Fast path: a plain single-codepoint ASCII glyph is always one column.
        if not (symbol.isascii() and len(symbol) == 1):
            skip_columns = max(grapheme_width(symbol) - 1, 0)
    if active is not None:
        if active.link:
            out.append(OSC8_CLOSE)
        if active != Style():
            out.append(_RESET)
    return "".join(out)
