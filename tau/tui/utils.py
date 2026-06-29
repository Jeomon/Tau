from __future__ import annotations

import difflib
import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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

# ── Text cursor block ─────────────────────────────────────────────────────────
# The editor draws its own text cursor (the real hardware cursor is hidden).
# When the terminal window has focus we draw a solid reverse-video block. When
# it loses focus we instead render a plain cell and let the renderer reveal the
# real hardware cursor, which the terminal itself draws as a full-cell hollow
# outline — matching the native unfocused-cursor look exactly. The terminal
# reports focus changes via DECSET 1004 (see Terminal.enable_focus_reporting);
# TUI feeds the result here, and Renderer reads is_window_focused() to decide
# whether to show the hardware cursor.
_window_focused = True


def set_window_focused(focused: bool) -> None:
    """Record whether the terminal window currently has focus."""
    global _window_focused
    _window_focused = focused


def is_window_focused() -> bool:
    """True when the terminal window has focus (defaults True if unreported)."""
    return _window_focused


def cursor_block(ch: str = " ") -> str:
    """Return the text-cursor cell for character ``ch`` under the cursor.

    Focused  → solid reverse-video block (``ch`` shown inverted).
    Unfocused→ the bare character/cell, so the terminal's own hardware cursor
    (revealed by the renderer while unfocused) draws its native hollow outline
    over it.
    """
    if _window_focused:
        return REVERSE + ch + "\x1b[27m"
    return ch


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
    Breaks at word boundaries when possible; falls back to hard split for long words.
    """
    prefix = tracker.active_codes()
    taken, col = "", 0
    i = 0

    # Word-boundary checkpoint: position in source text after last consumed space
    last_wb_i = -1
    last_wb_taken = ""
    last_wb_snap: tuple | None = None

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
        # Record word boundary after consuming each space
        if ch == " ":
            last_wb_i = i
            last_wb_taken = taken
            last_wb_snap = tracker.snapshot()

    # Stopped mid-word: snap back to last space boundary if one exists
    if i < len(text) and last_wb_i != -1:
        tracker.restore(last_wb_snap)  # type: ignore[arg-type]
        taken = last_wb_taken
        i = last_wb_i

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

    def snapshot(self) -> tuple:
        return (self._bold, self._dim, self._italic, self._underline, self._fg, self._bg)

    def restore(self, snap: tuple) -> None:
        self._bold, self._dim, self._italic, self._underline, self._fg, self._bg = snap

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


# ── Project utilities ─────────────────────────────────────────────────────────


def project_name() -> str:
    """Best-effort project name: git repo root dir name, else cwd dir name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd().name


# ── Fuzzy matching ─────────────────────────────────────────────────────────────


@dataclass
class FuzzyMatch:
    matches: bool
    score: float


def fuzzy_match(query: str, text: str) -> FuzzyMatch:
    """
    Match if all query characters appear in order (not necessarily consecutive).
    Lower score = better match.  Rewards consecutive runs and word-boundary hits.
    """
    q = query.lower()
    t = text.lower()

    def _match(q: str) -> FuzzyMatch:
        if not q:
            return FuzzyMatch(True, 0)
        if len(q) > len(t):
            return FuzzyMatch(False, 0)

        qi = 0
        score = 0.0
        last = -1
        consecutive = 0

        for i, ch in enumerate(t):
            if qi < len(q) and ch == q[qi]:
                is_boundary = i == 0 or bool(re.match(r"[\s\-_./:]", t[i - 1]))
                if last == i - 1:
                    consecutive += 1
                    score -= consecutive * 5
                else:
                    consecutive = 0
                    if last >= 0:
                        score += (i - last - 1) * 2
                if is_boundary:
                    score -= 10
                score += i * 0.1
                last = i
                qi += 1

        if qi < len(q):
            return FuzzyMatch(False, 0)
        if q == t:
            score -= 100
        return FuzzyMatch(True, score)

    result = _match(q)
    if result.matches:
        return result

    # Try swapped alphanumeric order (e.g. "v3" matches "3v")
    m = re.match(r"^(?P<a>[a-z]+)(?P<d>[0-9]+)$", q) or re.match(r"^(?P<d>[0-9]+)(?P<a>[a-z]+)$", q)
    if m:
        swapped = (m.group("d") + m.group("a")) if "a" in m.groupdict() else ""
        if swapped:
            alt = _match(swapped)
            if alt.matches:
                return FuzzyMatch(True, alt.score + 5)

    return result


def fuzzy_filter(items: list, query: str, get_text) -> list:
    """
    Filter and sort items by fuzzy match quality (best matches first).
    Supports space-separated tokens — all tokens must match.
    `get_text` is a callable that extracts the searchable string from an item.
    """
    if not query.strip():
        return items

    tokens = [t for t in query.strip().split() if t]
    if not tokens:
        return items

    scored: list[tuple[object, float]] = []
    for item in items:
        text = get_text(item)
        total = 0.0
        ok = True
        for token in tokens:
            m = fuzzy_match(token, text)
            if m.matches:
                total += m.score
            else:
                ok = False
                break
        if ok:
            scored.append((item, total))

    scored.sort(key=lambda x: x[1])
    return [item for item, _ in scored]


# ── Diff rendering ─────────────────────────────────────────────────────────────

# Matches a standard unified diff line: prefix (+/-/ ) followed by optional
# line number then content.
_UNIFIED_LINE = re.compile(r"^([+\- ])(\s*\d*)\s?(.*)$")


def _is_diff(text: str) -> bool:
    """Heuristic: return True if text looks like a unified diff."""
    lines = text.splitlines()
    has_marker = any(ln.startswith(("---", "+++", "@@")) for ln in lines[:20])
    has_change = any(ln.startswith(("+", "-")) and len(ln) > 1 for ln in lines[:20])
    return has_marker and has_change


def _word_diff(old: str, new: str, inverse: Callable[[str], str]) -> tuple[str, str]:
    """Highlight changed words with inverse video."""
    old_words = re.split(r"(\s+)", old)
    new_words = re.split(r"(\s+)", new)
    sm = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)

    old_out, new_out = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_chunk = "".join(old_words[i1:i2])
        new_chunk = "".join(new_words[j1:j2])
        if tag == "equal":
            old_out.append(old_chunk)
            new_out.append(new_chunk)
        elif tag == "replace":
            old_out.append(inverse(old_chunk) if old_chunk.strip() else old_chunk)
            new_out.append(inverse(new_chunk) if new_chunk.strip() else new_chunk)
        elif tag == "delete":
            old_out.append(inverse(old_chunk) if old_chunk.strip() else old_chunk)
        elif tag == "insert":
            new_out.append(inverse(new_chunk) if new_chunk.strip() else new_chunk)

    return "".join(old_out), "".join(new_out)


def render_diff(
    diff_text: str,
    added: Callable[[str], str],
    removed: Callable[[str], str],
    context: Callable[[str], str],
    hunk: Callable[[str], str],
    inverse: Callable[[str], str],
) -> list[str]:
    """
    Render a unified diff string with ANSI colors.

    - Header lines (---, +++) and hunk markers (@@): styled with ``hunk``
    - Added lines (+): styled with ``added``
    - Removed lines (-): styled with ``removed``
    - Context lines ( ): styled with ``context``
    - Adjacent single-line add/remove pairs get intra-line word highlighting
      via ``inverse``.
    """
    raw_lines = diff_text.splitlines()
    result: list[str] = []
    i = 0

    while i < len(raw_lines):
        line = raw_lines[i]

        # Header / hunk marker
        if line.startswith(("---", "+++", "@@", "diff ", "index ", "new file", "deleted file")):
            result.append(hunk(line))
            i += 1
            continue

        if line.startswith("-"):
            # Collect consecutive removed lines
            removed_lines: list[str] = []
            while i < len(raw_lines) and raw_lines[i].startswith("-"):
                removed_lines.append(raw_lines[i][1:])
                i += 1

            # Collect consecutive added lines that immediately follow
            added_lines: list[str] = []
            while i < len(raw_lines) and raw_lines[i].startswith("+"):
                added_lines.append(raw_lines[i][1:])
                i += 1

            # Intra-line diff only when it's a 1:1 change
            if len(removed_lines) == 1 and len(added_lines) == 1:
                old_hi, new_hi = _word_diff(removed_lines[0], added_lines[0], inverse)
                result.append(removed("-" + old_hi))
                result.append(added("+" + new_hi))
            else:
                for ln in removed_lines:
                    result.append(removed("-" + ln))
                for ln in added_lines:
                    result.append(added("+" + ln))
            continue

        if line.startswith("+"):
            result.append(added(line))
            i += 1
            continue

        if line.startswith(" "):
            result.append(context(line))
            i += 1
            continue

        # Unrecognised line — pass through dimmed
        result.append(context(line))
        i += 1

    return result
