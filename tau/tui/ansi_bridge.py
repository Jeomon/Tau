"""Bidirectional bridge between legacy ANSI-string lines and real Buffer/Cell content.

Stage 2 of the TUI migration lets Buffer-native components (``render_cells``)
and not-yet-migrated legacy components (``render(width) -> list[str]``)
coexist in the same tree. Two conversions make that possible:

- ``parse_ansi_into``: legacy ANSI output -> real ``Cell``/``Style`` objects,
  written into a target ``Buffer`` row. Used by ``Component``'s default
  ``render_cells`` so unmigrated components keep working under Buffer-based
  containers without any changes to the component itself.
- ``row_to_ansi``: a ``Buffer`` row -> an ANSI string. Used by ``Component``'s
  default ``render(width)`` so newly-migrated components stay callable by any
  code that hasn't moved onto the Buffer contract yet.

The SGR parser here mirrors ``utils.py``'s ``_AnsiStateTracker`` (used for
line-wrap state carry-over) but resolves into a real ``tau.tui.style.Style``
instead of loose booleans/raw code strings, and additionally tracks
reversed/strikethrough/blink and underline color, which ``_AnsiStateTracker``
does not need for its own (wrap-only) purpose.
"""

from __future__ import annotations

import re

import grapheme

from tau.tui.buffer import Buffer, RawWrite
from tau.tui.style import OSC8_CLOSE, Color, Modifier, Style, style_transition
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


class _SgrState:
    """Accumulates SGR/OSC-8 escapes into a live ``Style``, one code run at a time."""

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


def parse_ansi_into(buf: Buffer, x: int, y: int, line: str, max_width: int) -> int:
    """Parse an ANSI-laden ``line`` into real ``Cell``s written at row ``y`` from column ``x``.

    Mirrors ``Buffer.set_string``'s grapheme-cluster/double-width handling,
    but scans SGR/OSC-8 escapes to resolve each cluster's ``Style`` instead of
    taking one pre-resolved style for the whole call. Returns the column after
    the last glyph written.

    A line carrying a Kitty/iTerm2 inline-image escape is treated as one
    atomic RawWrite covering the whole string — a legacy component
    (``MessageBlock``) can embed an ``Image``'s already-rendered line
    verbatim, and the image payload must survive the round trip rather
    than being silently eaten as an unrecognized no-op escape. Checked
    by substring rather than prefix: the iTerm2 line in particular leads
    with a relative cursor-up move (``Image``'s "position, then draw"
    trick for a protocol with no explicit placement params) before the
    OSC 1337 sequence itself.
    """
    buf.grow_to(y + 1)
    if "\x1b_G" in line or "\x1b]1337;File=" in line:
        token = f"legacy-image:{hash(line)}"
        buf.raw_writes.append(RawWrite(x, y, line, token))
        return x

    state = _SgrState()
    limit = min(buf.area.right, x + max_width)
    col = x
    i = 0
    n = len(line)
    while i < n and col < limit:
        m = _ANSI_RE.match(line, i)
        if m:
            state.process(m.group(0))
            i += len(m.group(0))
            continue
        # Consume one grapheme cluster starting at i (no ANSI inside it).
        rest = line[i:]
        cluster = next(iter(grapheme.graphemes(rest)), "")
        if not cluster:
            i += 1
            continue
        w = grapheme_width(cluster)
        i += len(cluster)
        if w == 0:
            continue
        if col + w > limit:
            break
        buf.set(col, y, cluster, state.style)
        if w == 2 and col + 1 < limit:
            buf.set(col + 1, y, "", state.style)
        col += w
    return col


def parse_ansi_wrapped_into(buf: Buffer, x: int, y: int, line: str, max_width: int) -> int:
    """Parse ANSI text into styled cells, wrapping overflow across buffer rows.

    Returns the number of rows written. Wrapping is performed on styled
    grapheme tokens, so no ANSI string is reconstructed or reparsed per row.
    """
    if max_width <= 0:
        return 0
    if "\x1b_G" in line or "\x1b]1337;File=" in line:
        buf.grow_to(y + 1)
        token = f"legacy-image:{hash(line)}"
        buf.raw_writes.append(RawWrite(x, y, line, token))
        return 1

    state = _SgrState()
    tokens: list[tuple[str, int, Style]] = []
    index = 0
    while index < len(line):
        match = _ANSI_RE.match(line, index)
        if match:
            state.process(match.group(0))
            index += len(match.group(0))
            continue
        cluster = next(iter(grapheme.graphemes(line[index:])), "")
        if not cluster:
            index += 1
            continue
        index += len(cluster)
        width = grapheme_width(cluster)
        if width:
            tokens.append((cluster, width, state.style))

    if not tokens:
        buf.grow_to(y + 1)
        return 1

    indent_end = 0
    while indent_end < len(tokens) and tokens[indent_end][0].isspace():
        indent_end += 1
    indent = tokens[:indent_end]
    indent_width = sum(token[1] for token in indent)
    if indent_width >= max_width:
        indent = []
        indent_width = 0

    remaining = tokens
    row = 0
    first = True
    while remaining:
        prefix = [] if first else indent
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

        row_tokens = [*prefix, *remaining[:taken]]
        buf.grow_to(y + row + 1)
        col = x
        limit = x + max_width
        for cluster, width, style in row_tokens:
            if col + width > limit:
                break
            buf.set(col, y + row, cluster, style)
            if width == 2 and col + 1 < limit:
                buf.set(col + 1, y + row, "", style)
            col += width
        remaining = remaining[taken:]
        row += 1
        first = False
    return row


def row_to_ansi(buf: Buffer, y: int, cursor_x: int | None = None, *, embed_raw: bool = True) -> str:
    """Flatten one ``Buffer`` row back into an ANSI string (skip cells excluded).

    Double-width glyphs occupy two cells (the glyph, then a continuation
    placeholder — see ``Buffer.set_string``); ``Cell.set_symbol`` coerces an
    empty placeholder symbol to ``" "``, so the continuation cell can't be
    told apart from an intentional space by symbol alone. Track the glyph
    width instead (the same technique ``Buffer.diff`` uses) and skip the
    placeholder rather than printing a stray extra space.

    ``cursor_x``, when given, embeds ``CURSOR_MARKER`` right before that
    column — the legacy ``render(width) -> list[str]`` bridge's way of
    carrying a Buffer-native component's ``cursor_position`` through to
    callers that haven't moved onto ``render_cells`` yet (e.g. ``Layout``
    before Stage 5), so IME cursor placement keeps working either way.

    Any ``buf.raw_writes`` anchored to this row (e.g. an inline image's
    escape sequence — see ``Image``) are spliced in at their column
    verbatim by default, same reasoning: legacy callers have no other way
    to see the bytes since the cells underneath are ``skip=True`` and
    carry no symbol. ``ScrollbackTerminal`` passes ``embed_raw=False``
    since it has its own novelty-tracked flush for raw writes (resending a
    multi-MB image payload every time an unrelated cell nearby changes
    would be wasteful, unlike plain text which is cheap to resend as-is).
    """
    from tau.tui.utils import CURSOR_MARKER

    raw_at = {rw.x: rw.data for rw in buf.raw_writes if rw.y == y} if embed_raw else {}

    out: list[str] = []
    active: Style | None = None
    skip_cols = 0
    for x in range(buf.area.left, buf.area.right):
        if cursor_x is not None and x == cursor_x:
            out.append(CURSOR_MARKER)
        if x in raw_at:
            out.append(raw_at[x])
        cell = buf.get(x, y)
        if cell.skip:
            continue
        if skip_cols > 0:
            skip_cols -= 1
            continue
        if cell.style != active:
            out.append(style_transition(active, cell.style))
            active = cell.style
        symbol = cell.symbol or " "
        out.append(symbol)
        skip_cols = max(grapheme_width(symbol) - 1, 0)
    if cursor_x is not None and cursor_x >= buf.area.right:
        out.append(CURSOR_MARKER)
    if active is not None:
        if active.link:
            out.append(OSC8_CLOSE)
        if active != Style():
            out.append(_RESET)
    return "".join(out)
