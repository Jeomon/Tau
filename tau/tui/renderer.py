from __future__ import annotations

from tau.tui.ansi import (
    _ANSI_RE,
    CURSOR_MARKER,
    RESET,
    _char_width,  # type: ignore[attr-defined]
    is_window_focused,
    truncate,
    visible_width,
)
from tau.tui.ansi import (
    wrap as _wrap,
)
from tau.tui.component import Component
from tau.tui.terminal import Terminal

_CURSOR_MARKER_LEN = len(CURSOR_MARKER)

# Blank columns reserved on the left/right edges of the terminal so content
# never touches the window border.
_LEFT_PAD = 1
_RIGHT_PAD = 1


class Renderer:
    """
    Scrollback-mode differential renderer.

    Renders into the main terminal buffer — no alternate screen.  Content
    grows downward; old lines scroll into the terminal's native scrollback
    buffer so the user can scroll back with the terminal's own scrollbar.

    Positioning uses only relative cursor moves (ESC[NA / ESC[NB) so the
    terminal's own scroll state is never disrupted.  Overlays are composited
    directly into the content lines before the diff, keeping a single
    rendering pass.
    """

    def __init__(self, terminal: Terminal, show_hardware_cursor: bool = False) -> None:
        self._terminal = terminal
        self._show_hardware_cursor = show_hardware_cursor
        self._prev_lines: list[str] = []
        self._cursor_row: int = 0  # logical index of last rendered line
        self._hw_cursor_row: int = 0  # where the terminal cursor actually is
        self._viewport_top: int = 0  # first logical line visible on screen
        self._max_lines: int = 0
        self._prev_width: int = 0
        self._prev_height: int = 0
        self._resized: bool = False
        # Memoizes the width-wrap of each line (line -> wrapped rows). Unchanged
        # blocks emit stable string objects, so this skips the costly visible_width
        # ANSI scan for every line that didn't change since the last frame.
        self._clamp_cache: dict[str, list[str]] = {}
        self._clamp_cache_width: int = 0
        self._unsub_resize = terminal.on_resize(self._on_resize)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def render(self, component: Component, overlays: list | None = None) -> None:
        """Render component differentially into the terminal scrollback buffer."""
        width = self._terminal.width - _LEFT_PAD - _RIGHT_PAD
        height = self._terminal.height
        width_changed = self._resized or (self._prev_width != 0 and self._prev_width != width)
        self._resized = False

        new_lines: list[str] = component.render(width)

        # Expand every line that exceeds the terminal width into multiple physical
        # rows by wrapping rather than truncating.  A raw overflow line causes the
        # terminal to auto-wrap onto extra physical rows, desynchronising
        # _hw_cursor_row from the real hardware cursor row and corrupting the
        # differential renderer.  By expanding here we keep the logical-line ↔
        # physical-row mapping exact and the user sees the full content.
        #
        # `visible_width` runs an ANSI regex per line, which dominates frame cost
        # on long transcripts.  Memoize the result by line value so unchanged
        # lines (stable cached-block strings) skip the scan entirely.
        if width != self._clamp_cache_width:
            self._clamp_cache = {}
            self._clamp_cache_width = width
        prev_clamp = self._clamp_cache
        clamp: dict[str, list[str]] = {}
        clamped: list[str] = []
        for line in new_lines:
            wrapped = prev_clamp.get(line)
            if wrapped is None:
                if visible_width(line) > width:
                    wrapped = _wrap(line, width)
                    clamp[line] = wrapped
                    clamped.extend(wrapped)
                else:
                    clamped.append(line)
                continue
            clamp[line] = wrapped
            clamped.extend(wrapped)
        self._clamp_cache = clamp
        new_lines = clamped

        # Always have at least one line so index arithmetic stays valid.
        if not new_lines:
            new_lines = [""]

        # Composite overlays into the visible viewport portion of new_lines.
        if overlays:
            new_lines = self._composite_overlays(new_lines, overlays, width, height)

        # Locate and strip CURSOR_MARKER (TextInput IME cursor position).
        cursor_pos: tuple[int, int] | None = None
        for _r, _line in enumerate(new_lines):
            if CURSOR_MARKER in _line:
                _mi = _line.index(CURSOR_MARKER)
                cursor_pos = (_r, visible_width(_line[:_mi]) + _LEFT_PAD)
                new_lines[_r] = _line[:_mi] + _line[_mi + _CURSOR_MARKER_LEN :]
                break

        # Reserve the left/right margins on every line.
        left_pad = " " * _LEFT_PAD
        right_pad = " " * _RIGHT_PAD
        new_lines = [left_pad + line + right_pad for line in new_lines]

        # First render (or after reset()).
        if not self._prev_lines and not width_changed:
            self._full_render(new_lines, cursor_pos, width, height, clear=False)
            return

        # Width changed — wrapping changes, must fully redraw.
        if width_changed:
            self._full_render(new_lines, cursor_pos, width, height, clear=True)
            return

        prev = self._prev_lines

        # Find first and last changed line.
        max_len = max(len(new_lines), len(prev))
        first_changed = -1
        last_changed = -1
        for i in range(max_len):
            old_line = prev[i] if i < len(prev) else ""
            new_line = new_lines[i] if i < len(new_lines) else ""
            if old_line != new_line:
                if first_changed == -1:
                    first_changed = i
                last_changed = i

        # No content changes — only reposition IME cursor if needed.
        if first_changed == -1:
            self._position_hw_cursor(cursor_pos, new_lines)
            self._prev_height = height
            return

        # Changed line is above the visible viewport — full redraw.
        if first_changed < self._viewport_top:
            self._full_render(new_lines, cursor_pos, width, height, clear=True)
            return

        # === Differential render ===
        buf = self._terminal.begin_sync()

        viewport_top = self._viewport_top
        hw_cursor = self._hw_cursor_row
        viewport_bottom = viewport_top + height - 1

        # If first changed row is beyond the viewport bottom, scroll down to it.
        if first_changed > viewport_bottom:
            current_screen_row = hw_cursor - viewport_top
            move_to_bottom = max(0, (height - 1) - current_screen_row)
            if move_to_bottom > 0:
                buf += f"\x1b[{move_to_bottom}B"
            scroll = first_changed - viewport_bottom
            buf += "\r\n" * scroll
            viewport_top += scroll
            hw_cursor = first_changed
            viewport_bottom = viewport_top + height - 1

        # Move cursor up/down to the first changed line.
        line_diff = first_changed - hw_cursor
        if line_diff > 0:
            buf += f"\x1b[{line_diff}B"
        elif line_diff < 0:
            buf += f"\x1b[{-line_diff}A"
        buf += "\r"
        hw_cursor = first_changed

        render_end = min(last_changed, len(new_lines) - 1)
        for i in range(first_changed, render_end + 1):
            if i > first_changed:
                buf += "\r\n"
                hw_cursor += 1
            buf += "\x1b[2K"
            if i < len(new_lines) and new_lines[i]:
                buf += new_lines[i]

        final_cursor_row = render_end

        # Clear extra lines if content shrank.
        if len(prev) > len(new_lines):
            if render_end < len(new_lines) - 1:
                move_down = len(new_lines) - 1 - render_end
                buf += f"\x1b[{move_down}B"
                final_cursor_row = len(new_lines) - 1
            extra = len(prev) - len(new_lines)
            for _ in range(extra):
                buf += "\r\n\x1b[2K"
            buf += f"\x1b[{extra}A"

        buf += self._terminal.end_sync()
        self._terminal.write(buf)

        self._cursor_row = max(0, len(new_lines) - 1)
        self._hw_cursor_row = final_cursor_row
        self._max_lines = max(self._max_lines, len(new_lines))
        self._viewport_top = max(viewport_top, final_cursor_row - height + 1)
        self._prev_lines = new_lines
        self._prev_width = width
        self._prev_height = height

        self._position_hw_cursor(cursor_pos, new_lines)

    def clear(self) -> None:
        """Erase the entire screen and scrollback buffer."""
        self._terminal.write_flush(
            self._terminal.begin_sync() + "\x1b[2J\x1b[H\x1b[3J" + self._terminal.end_sync()
        )
        self._prev_lines = []
        self._cursor_row = 0
        self._hw_cursor_row = 0
        self._viewport_top = 0
        self._max_lines = 0

    def reset(self) -> None:
        """Force a full re-render on the next frame without clearing the screen."""
        self._prev_lines = []
        self._cursor_row = 0
        self._hw_cursor_row = 0
        self._viewport_top = 0

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _full_render(
        self,
        new_lines: list[str],
        cursor_pos: tuple[int, int] | None,
        width: int,
        height: int,
        *,
        clear: bool,
    ) -> None:
        buf = self._terminal.begin_sync()
        if clear:
            buf += "\x1b[2J\x1b[H\x1b[3J"  # clear screen + scrollback
        else:
            buf += "\r"  # start from column 0 for first render
        for i, line in enumerate(new_lines):
            if i > 0:
                buf += "\r\n"
            buf += "\x1b[2K"
            buf += line
        buf += self._terminal.end_sync()
        self._terminal.write(buf)

        self._cursor_row = max(0, len(new_lines) - 1)
        self._hw_cursor_row = self._cursor_row
        self._max_lines = len(new_lines) if clear else max(self._max_lines, len(new_lines))
        buf_len = max(height, len(new_lines))
        self._viewport_top = max(0, buf_len - height)
        self._prev_lines = new_lines
        self._prev_width = width
        self._prev_height = height

        self._position_hw_cursor(cursor_pos, new_lines)

    def _position_hw_cursor(self, cursor_pos: tuple[int, int] | None, new_lines: list[str]) -> None:
        """Move the hardware terminal cursor to the IME position and show/hide it."""
        if cursor_pos is None or not new_lines:
            self._terminal.write_flush("\x1b[?25l")
            return

        target_row, target_col = cursor_pos
        target_row = max(0, min(target_row, len(new_lines) - 1))

        row_delta = target_row - self._hw_cursor_row
        buf = ""
        if row_delta > 0:
            buf += f"\x1b[{row_delta}B"
        elif row_delta < 0:
            buf += f"\x1b[{-row_delta}A"
        buf += f"\x1b[{target_col + 1}G"  # absolute column (1-indexed)
        # Reveal the real hardware cursor when the window is unfocused: the
        # terminal draws it as a hollow outline, giving the native unfocused
        # cursor look. While focused we keep it hidden and draw our own block.
        if self._show_hardware_cursor or not is_window_focused():
            buf += "\x1b[?25h"  # show cursor
        else:
            buf += "\x1b[?25l"  # hide cursor (we draw our own block)
        self._terminal.write_flush(buf)
        self._hw_cursor_row = target_row

    def _composite_overlays(
        self, lines: list[str], overlays: list, width: int, height: int
    ) -> list[str]:
        """Composite all visible overlays into the visible portion of lines."""
        viewport_start = max(0, len(lines) - height)
        result = list(lines)

        for entry in overlays:
            if not entry.is_visible(width, height):
                continue
            ov_w = entry.resolve_width(width)
            all_ov_lines = entry.component.render(ov_w)
            ov_w2, ov_h, ov_row, ov_col = entry.resolve(width, height, len(all_ov_lines))
            ov_lines = all_ov_lines[:ov_h]

            for i, ov_line in enumerate(ov_lines):
                logical = viewport_start + ov_row + i
                if logical < 0:
                    continue
                while logical >= len(result):
                    result.append("")
                result[logical] = _composite_line(
                    result[logical], _fit_line(ov_line, ov_w2), ov_col, ov_w2, width
                )

        return result

    def _on_resize(self) -> None:
        # Clear state; next render() call forces a full clear+redraw, even if
        # the reported width didn't change (e.g. a height-only resize), so a
        # stale frame is never left on screen for the new render to stack atop.
        self._prev_lines = []
        self._cursor_row = 0
        self._hw_cursor_row = 0
        self._viewport_top = 0
        self._resized = True


# ── Line helpers ──────────────────────────────────────────────────────────────


def _fit_line(line: str, width: int) -> str:
    """Pad or truncate a line to exactly width visible columns."""
    vw = visible_width(line)
    if vw > width:
        return truncate(line, width, ellipsis="")
    if vw < width:
        return line + RESET + " " * (width - vw)
    return line


def _split_at_column(text: str, col: int) -> tuple[str, str]:
    """ANSI-safe split: returns (text_before_col, text_from_col_onwards)."""
    vis = 0
    i = 0
    while i < len(text):
        m = _ANSI_RE.match(text, i)
        if m:
            i += len(m.group(0))
            continue
        ch = text[i]
        w = _char_width(ch)
        if vis + w > col:
            break
        vis += w
        i += 1
    return text[:i], text[i:]


def _composite_line(base: str, overlay: str, col: int, ov_width: int, total_width: int) -> str:
    """Splice overlay into base starting at visual column col."""
    before, rest = _split_at_column(base, col)

    # Pad before to exactly col visual columns if base is shorter.
    before_vw = visible_width(before)
    if before_vw < col:
        before += " " * (col - before_vw)

    # Skip the overlay zone in the base to get the "after" segment.
    _, after = _split_at_column(rest, ov_width)

    # Overlay is already padded to ov_width by _fit_line; just truncate if needed.
    ov_vw = visible_width(overlay)
    if ov_vw > ov_width:
        overlay = truncate(overlay, ov_width, ellipsis="")

    result = before + RESET + overlay + RESET + after
    if visible_width(result) > total_width:
        result = truncate(result, total_width, ellipsis="")
    return result
