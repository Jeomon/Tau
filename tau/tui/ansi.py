from __future__ import annotations

import re
import unicodedata

# ── Regex to strip all ANSI escape sequences ─────────────────────────────────
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;:<=>?]*[ -/]*[@-~]"  # CSI sequences
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|_[^\x1b]*(?:\x1b\\|\x07)"  # APC sequences
    r"|[PX^][^\x1b]*\x1b\\"  # DCS / PM / SOS
    r"|[@-_]"  # 2-char Fe sequences (ESC @-_)
    r")"
)

# ── Cursor marker ────────────────────────────────────────────────────────────
# An APC sequence (zero visible width, stripped by _ANSI_RE) injected into a
# rendered line to tell the Renderer where to position the hardware cursor for
# IME candidate windows.  TextInput inserts it; Renderer removes it and moves
# the real terminal cursor to the marked column.
CURSOR_MARKER = "\x1b_C\x1b\\"

# ── SGR (colour / style) ──────────────────────────────────────────────────────

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"
BLINK = "\x1b[5m"
REVERSE = "\x1b[7m"
STRIKE = "\x1b[9m"

# Standard foreground colours
BLACK = "\x1b[30m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"
DEFAULT = "\x1b[39m"

# Bright foreground colours
BRIGHT_BLACK = "\x1b[90m"
BRIGHT_RED = "\x1b[91m"
BRIGHT_GREEN = "\x1b[92m"
BRIGHT_YELLOW = "\x1b[93m"
BRIGHT_BLUE = "\x1b[94m"
BRIGHT_MAGENTA = "\x1b[95m"
BRIGHT_CYAN = "\x1b[96m"
BRIGHT_WHITE = "\x1b[97m"

# Standard background colours
BG_BLACK = "\x1b[40m"
BG_RED = "\x1b[41m"
BG_GREEN = "\x1b[42m"
BG_YELLOW = "\x1b[43m"
BG_BLUE = "\x1b[44m"
BG_MAGENTA = "\x1b[45m"
BG_CYAN = "\x1b[46m"
BG_WHITE = "\x1b[47m"
BG_DEFAULT = "\x1b[49m"


def fg(r: int, g: int, b: int) -> str:
    """Truecolor foreground."""
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(r: int, g: int, b: int) -> str:
    """Truecolor background."""
    return f"\x1b[48;2;{r};{g};{b}m"


def fg256(code: int) -> str:
    """256-colour foreground (0–255)."""
    return f"\x1b[38;5;{code}m"


def bg256(code: int) -> str:
    """256-colour background (0–255)."""
    return f"\x1b[48;5;{code}m"


def style(text: str, *codes: str) -> str:
    """Wrap text with one or more SGR codes, reset at end."""
    return "".join(codes) + text + RESET


# ── Width calculation ─────────────────────────────────────────────────────────


def _char_width(ch: str) -> int:
    """Return the terminal column width of a single character."""
    cp = ord(ch)
    # Null byte / C0 / C1 control chars
    if cp == 0 or (0x007F <= cp <= 0x009F):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    if (eaw == "Na" or unicodedata.category(ch) in ("Mn", "Me", "Cf")) and unicodedata.category(
        ch
    ) in ("Mn", "Me", "Cf"):
        # Combining marks and format chars have zero width
        return 0
    return 1


def visible_width(text: str) -> int:
    """Return the number of terminal columns the string will occupy."""
    # Fast path: pure ASCII printable
    if text.isascii() and text.isprintable():
        return len(text)
    stripped = strip_ansi(text)
    return sum(_char_width(ch) for ch in stripped)


# ── Strip ─────────────────────────────────────────────────────────────────────


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


# ── Truncation ────────────────────────────────────────────────────────────────


def truncate(text: str, max_width: int, ellipsis: str = "…") -> str:
    """
    Truncate text to max_width columns, preserving ANSI codes.
    Appends ellipsis if truncation occurred.
    """
    if visible_width(text) <= max_width:
        return text

    ellipsis_w = visible_width(ellipsis)
    target = max_width - ellipsis_w
    result, _ = _take_columns(text, target)
    return result + RESET + ellipsis + RESET


def pad(text: str, width: int, char: str = " ", align: str = "left") -> str:
    """
    Pad text to exactly width columns with char.
    align: 'left' | 'right' | 'center'
    """
    current = visible_width(text)
    deficit = max(0, width - current)
    if align == "right":
        return char * deficit + text
    if align == "center":
        left = deficit // 2
        right = deficit - left
        return char * left + text + char * right
    return text + char * deficit


# ── Wrapping ──────────────────────────────────────────────────────────────────


def wrap(text: str, width: int) -> list[str]:
    """
    Wrap text to width columns, preserving ANSI escape codes across line breaks.
    Hard newlines in the source are respected.
    """
    if width <= 0:
        return [text]

    result: list[str] = []
    for source_line in text.split("\n"):
        result.extend(_wrap_single_line(source_line, width))
    return result


def _wrap_single_line(line: str, width: int) -> list[str]:
    """Wrap a single line (no embedded newlines) to width columns."""
    if visible_width(line) <= width:
        return [line]

    lines: list[str] = []
    tracker = _AnsiStateTracker()
    remaining = line

    while remaining:
        chunk, remaining = _split_at_columns(remaining, width, tracker)
        lines.append(chunk)

    return lines


def _split_at_columns(text: str, width: int, tracker: _AnsiStateTracker) -> tuple[str, str]:
    """
    Split text into (head, tail) where head fits within width columns.
    Tracker maintains active SGR state so head starts with the right codes
    and tail can be resumed correctly.
    """
    prefix = tracker.active_codes()
    taken, col = "", 0
    i = 0

    while i < len(text):
        # Check for ANSI escape at current position
        m = _ANSI_RE.match(text, i)
        if m:
            code = m.group(0)
            tracker.process(code)
            taken += code
            i += len(code)
            continue

        ch = text[i]
        w = _char_width(ch)
        if col + w > width:
            break
        taken += ch
        col += w
        i += 1

    head = prefix + taken + (RESET if tracker.has_state() else "")
    tail = text[i:]
    return head, tail


def _take_columns(text: str, max_cols: int) -> tuple[str, int]:
    """Return (text_up_to_max_cols, actual_cols_taken), preserving ANSI codes."""
    result, col = "", 0
    i = 0
    while i < len(text):
        m = _ANSI_RE.match(text, i)
        if m:
            result += m.group(0)
            i += len(m.group(0))
            continue
        ch = text[i]
        w = _char_width(ch)
        if col + w > max_cols:
            break
        result += ch
        col += w
        i += 1
    return result, col


# ── ANSI state tracker ────────────────────────────────────────────────────────


class _AnsiStateTracker:
    """
    Tracks active SGR (colour/style) state so it can be re-applied after
    a line break during wrapping.
    """

    def __init__(self) -> None:
        self._bold = False
        self._dim = False
        self._italic = False
        self._underline = False
        self._fg: str | None = None
        self._bg: str | None = None

    def process(self, code: str) -> None:
        m = re.match(r"\x1b\[([\d;]*)m$", code)
        if not m:
            return
        params = m.group(1)
        if not params or params == "0":
            self._reset()
            return
        nums = [int(x) for x in params.split(";") if x]
        i = 0
        while i < len(nums):
            n = nums[i]
            if n == 0:
                self._reset()
            elif n == 1:
                self._bold = True
            elif n == 2:
                self._dim = True
            elif n == 3:
                self._italic = True
            elif n == 4:
                self._underline = True
            elif n == 22:
                self._bold = self._dim = False
            elif n == 23:
                self._italic = False
            elif n == 24:
                self._underline = False
            elif 30 <= n <= 37 or n == 39 or 90 <= n <= 97:
                self._fg = f"\x1b[{n}m"
            elif n == 38 and i + 2 < len(nums) and nums[i + 1] == 5:
                self._fg = f"\x1b[38;5;{nums[i + 2]}m"
                i += 2
            elif n == 38 and i + 4 < len(nums) and nums[i + 1] == 2:
                self._fg = f"\x1b[38;2;{nums[i + 2]};{nums[i + 3]};{nums[i + 4]}m"
                i += 4
            elif 40 <= n <= 47 or n == 49 or 100 <= n <= 107:
                self._bg = f"\x1b[{n}m"
            elif n == 48 and i + 2 < len(nums) and nums[i + 1] == 5:
                self._bg = f"\x1b[48;5;{nums[i + 2]}m"
                i += 2
            elif n == 48 and i + 4 < len(nums) and nums[i + 1] == 2:
                self._bg = f"\x1b[48;2;{nums[i + 2]};{nums[i + 3]};{nums[i + 4]}m"
                i += 4
            i += 1

    def _reset(self) -> None:
        self._bold = self._dim = self._italic = self._underline = False
        self._fg = self._bg = None

    def has_state(self) -> bool:
        return bool(
            self._bold or self._dim or self._italic or self._underline or self._fg or self._bg
        )

    def active_codes(self) -> str:
        if not self.has_state():
            return ""
        parts = []
        if self._bold:
            parts.append(BOLD)
        if self._dim:
            parts.append(DIM)
        if self._italic:
            parts.append(ITALIC)
        if self._underline:
            parts.append(UNDERLINE)
        if self._fg:
            parts.append(self._fg)
        if self._bg:
            parts.append(self._bg)
        return "".join(parts)
