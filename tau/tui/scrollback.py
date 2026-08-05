"""String-based differential renderer for growing scrollback content.

Replaces the ``Cell``-grid renderer in ``frame.py``. Components hand this
already-styled ANSI *lines*; it diffs whole lines and repaints the ones that
changed. Nothing is ever parsed into per-character cells and serialised back.

Why
---
The cell grid cost a string -> Cell -> string round trip on every full repaint.
Measured on a ctrl+O expand of a large session: 1737 ms and ~3.2M Cell objects
via cells, versus 33 ms for the same output as strings — a 52x difference in
the same interpreter. Line-granular diffing (repaint a changed line whole,
rather than diffing its columns) is what makes that possible, and is what
``pi-tui`` does.

The trade: a one-character change repaints its whole line (~100 bytes instead
of ~20). At 60fps that is single-digit KB/s — irrelevant next to the round trip
it removes.

Everything about *how* the terminal is driven is preserved from ``frame.py``:
relative cursor moves only (rows that scroll into native scrollback can never
be addressed again — CSI H addresses the visible screen), viewport tracking,
synchronized-output batching, and novelty-tracked raw writes for inline images.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.buffer import RawWrite
    from tau.tui.geometry import Position
    from tau.tui.terminal import Terminal

_IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or bool(os.environ.get("TERMUX_VERSION"))


def _window_focused() -> bool:
    from tau.tui.frame import is_window_focused

    return is_window_focused()


class ScrollbackRenderer:
    """Differentially paints a list of ANSI lines into the terminal's scrollback.

    Mirrors ``frame.ScrollbackTerminal``'s contract and terminal behaviour so it
    can be swapped in, but its frame representation is ``list[str]`` rather than
    a ``Buffer`` of ``Cell``.
    """

    def __init__(self, terminal: Terminal, show_hardware_cursor: bool = False) -> None:
        self._terminal = terminal
        self._show_hardware_cursor = show_hardware_cursor
        self._prev: list[str] | None = None
        self._hw_cursor_row: int = 0
        self._viewport_top: int = 0
        self._max_lines: int = 0
        self._prev_width: int = 0
        self._prev_height: int = 0
        self._resized: bool = False
        self._disposed = False
        # Last-sent token per (x, y) anchor — see RawWrite. Cleared whenever the
        # real screen is erased, since a drawn image no longer exists there.
        self._sent_raw: dict[tuple[int, int], str] = {}
        self._unsub_resize = terminal.on_resize(self._on_resize)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def render(
        self,
        lines: list[str],
        cursor_pos: Position | None = None,
        stable_through: int = 0,
        raw_writes: list[RawWrite] | None = None,
    ) -> None:
        """Paint ``lines``, rewriting only what changed since the last call.

        ``stable_through``: the caller guarantees rows ``[0, stable_through)``
        are identical to the previous call's, so the diff scan can skip them.
        That is what keeps cost proportional to live content rather than total
        session length. Unlike the cell renderer this needs no ``elided_range``
        companion — copying string references is free, so callers always pass
        the whole list and nothing has to be reinstated.
        """
        try:
            self._render(lines, cursor_pos, stable_through)
        finally:
            self._flush_raw_writes(raw_writes or [])

    def clear(self) -> None:
        """Erase the entire screen and scrollback buffer."""
        self._terminal.write_flush(
            self._terminal.begin_sync() + "\x1b[2J\x1b[H\x1b[3J" + self._terminal.end_sync()
        )
        self._prev = None
        self._hw_cursor_row = 0
        self._viewport_top = 0
        self._max_lines = 0
        self._sent_raw.clear()

    def reset(self) -> None:
        """Force a full re-render on the next frame without clearing the screen."""
        self._prev = None
        self._hw_cursor_row = 0
        self._viewport_top = 0

    def reset_with_clear(self) -> None:
        """Force a full clear-and-redraw on the next frame.

        Unlike reset(), this takes the clear=True path — homing the cursor
        before writing — required when content painted at arbitrary screen rows
        (e.g. an overlay) must be erased without a terminal resize event.
        """
        self.reset()
        self._resized = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._unsub_resize()
        self._prev = None

    # -------------------------------------------------------------------------
    # Render
    # -------------------------------------------------------------------------

    def _render(
        self,
        lines: list[str],
        cursor_pos: Position | None,
        stable_through: int,
    ) -> None:
        width = self._terminal.width
        height = self._terminal.height
        width_changed = self._resized or (self._prev_width != 0 and self._prev_width != width)
        self._resized = False

        new_rows = len(lines)

        if self._prev is None and not width_changed:
            self._full_render(lines, cursor_pos, width, height, clear=False)
            return

        if width_changed:
            self._full_render(lines, cursor_pos, width, height, clear=True)
            return

        prev = self._prev
        assert prev is not None
        prev_rows = len(prev)

        max_rows = max(new_rows, prev_rows)
        scan_start = max(0, min(stable_through, max_rows))
        first_changed = -1
        last_changed = -1
        for y in range(scan_start, max_rows):
            a = prev[y] if y < prev_rows else ""
            b = lines[y] if y < new_rows else ""
            if a != b:
                if first_changed == -1:
                    first_changed = y
                last_changed = y

        if first_changed == -1:
            self._commit(lines, width, height)
            self._position_hw_cursor(cursor_pos, new_rows)
            return

        if first_changed < self._viewport_top:
            if new_rows == prev_rows:
                if last_changed < self._viewport_top:
                    self._commit(lines, width, height)
                    self._position_hw_cursor(cursor_pos, new_rows)
                    return
                first_changed = self._viewport_top
            else:
                # A row-count change entirely above the viewport (e.g. expanding
                # a tool-call block that already scrolled off-screen) can't be
                # expressed with relative moves in general — but the common case
                # is a pure insertion/deletion: everything from the viewport
                # down is identical, just shifted by ``delta``. When that holds
                # the physical screen is already correct (a terminal can't
                # retroactively edit rows in native history anyway), so only our
                # row bookkeeping shifts. Skipping the write avoids the visible
                # "scrollback snaps to bottom" jump on every such edit.
                delta = new_rows - prev_rows
                vt = self._viewport_top
                tail_len = prev_rows - vt
                shifted_vt = vt + delta
                if (
                    tail_len >= 0
                    and shifted_vt >= 0
                    and shifted_vt + tail_len <= new_rows
                    and all(prev[vt + i] == lines[shifted_vt + i] for i in range(tail_len))
                ):
                    self._hw_cursor_row += delta
                    self._viewport_top = shifted_vt
                    self._max_lines = max(0, self._max_lines + delta)
                    self._commit(lines, width, height, keep_viewport=True)
                    self._position_hw_cursor(cursor_pos, new_rows)
                    return
                self._full_render(lines, cursor_pos, width, height, clear=True)
                return

        # === Differential paint ===
        out = self._terminal.begin_sync()

        viewport_top = self._viewport_top
        hw_cursor = self._hw_cursor_row
        viewport_bottom = viewport_top + height - 1

        if first_changed > viewport_bottom:
            current_screen_row = hw_cursor - viewport_top
            move_to_bottom = max(0, (height - 1) - current_screen_row)
            if move_to_bottom > 0:
                out += f"\x1b[{move_to_bottom}B"
            scroll = first_changed - viewport_bottom
            out += "\r\n" * scroll
            viewport_top += scroll
            hw_cursor = first_changed
            viewport_bottom = viewport_top + height - 1

        line_diff = first_changed - hw_cursor
        if line_diff > 0:
            out += f"\x1b[{line_diff}B"
        elif line_diff < 0:
            out += f"\x1b[{-line_diff}A"
        out += "\r"
        hw_cursor = first_changed

        render_end = min(last_changed, new_rows - 1)
        for y in range(first_changed, render_end + 1):
            if y > first_changed:
                out += "\r\n"
                hw_cursor += 1
            # Line-granular: erase the row and write it whole. The cell renderer
            # diffed column runs within a row to shave bytes; at these sizes
            # that optimisation cost more (in round-tripping) than it saved.
            out += "\x1b[2K" + lines[y]

        final_cursor_row = hw_cursor

        if prev_rows > new_rows:
            if final_cursor_row < new_rows - 1:
                move_down = new_rows - 1 - final_cursor_row
                out += f"\x1b[{move_down}B"
                final_cursor_row = new_rows - 1
            if final_cursor_row >= new_rows:
                # Empty repaint loop: the cursor already sits on the first
                # changed removed row — clear it in place before those below.
                out += "\r\x1b[2K"
            extra = prev_rows - 1 - final_cursor_row
            for _ in range(extra):
                out += "\r\n\x1b[2K"
            if extra > 0:
                out += f"\x1b[{extra}A"

        self._hw_cursor_row = final_cursor_row
        self._max_lines = max(self._max_lines, new_rows)
        self._viewport_top = max(viewport_top, final_cursor_row - height + 1)
        self._prev = lines
        self._prev_width = width
        self._prev_height = height

        out += self._hw_cursor_ansi(cursor_pos, new_rows)
        out += self._terminal.end_sync()
        self._terminal.write(out)

    def _commit(
        self, lines: list[str], width: int, height: int, *, keep_viewport: bool = False
    ) -> None:
        self._prev = lines
        self._prev_width = width
        self._prev_height = height

    def _full_render(
        self,
        lines: list[str],
        cursor_pos: Position | None,
        width: int,
        height: int,
        *,
        clear: bool,
    ) -> None:
        rows = len(lines)
        if clear:
            self._sent_raw.clear()  # screen erased; a drawn image is gone with it
        out = self._terminal.begin_sync()
        if clear:
            out += "\x1b[2J\x1b[H\x1b[3J"  # clear screen + scrollback
        else:
            out += "\r"  # start from column 0 for the first render
        for i in range(rows):
            if i > 0:
                out += "\r\n"
            out += "\x1b[2K" + lines[i]

        self._hw_cursor_row = max(0, rows - 1)
        self._max_lines = rows if clear else max(self._max_lines, rows)
        self._viewport_top = max(0, max(height, rows) - height)
        self._prev = lines
        self._prev_width = width
        self._prev_height = height

        out += self._hw_cursor_ansi(cursor_pos, rows)
        out += self._terminal.end_sync()
        self._terminal.write(out)

    # -------------------------------------------------------------------------
    # Cursor
    # -------------------------------------------------------------------------

    def _position_hw_cursor(self, cursor_pos: Position | None, rows: int) -> None:
        self._terminal.write_flush(self._hw_cursor_ansi(cursor_pos, rows))

    def _hw_cursor_ansi(self, cursor_pos: Position | None, rows: int) -> str:
        """Compute the cursor move/show/hide sequence, updating _hw_cursor_row.

        Returned rather than written so callers can emit it inside the *same*
        begin_sync/end_sync batch as the content write — issuing it separately
        makes the real cursor visibly flicker on every frame while unfocused.
        """
        if cursor_pos is None or rows == 0:
            return "\x1b[?25l"

        target_row = max(0, min(cursor_pos.y, rows - 1))
        row_delta = target_row - self._hw_cursor_row
        out = ""
        if row_delta > 0:
            out += f"\x1b[{row_delta}B"
        elif row_delta < 0:
            out += f"\x1b[{-row_delta}A"
        out += f"\x1b[{cursor_pos.x + 1}G"  # absolute column (1-indexed)
        # Reveal the real cursor when unfocused: the terminal draws it hollow,
        # giving the native unfocused look. While focused we hide it and draw
        # our own block.
        if self._show_hardware_cursor or not _window_focused():
            out += "\x1b[?25h"
        else:
            out += "\x1b[?25l"
        self._hw_cursor_row = target_row
        return out

    # -------------------------------------------------------------------------
    # Raw writes (inline images)
    # -------------------------------------------------------------------------

    def _flush_raw_writes(self, raw_writes: list[RawWrite]) -> None:
        """Send any raw writes whose token changed since last sent.

        Independent of the line diff: an image's row carries no printable text,
        so it never registers as "changed" and needs its own novelty check.
        Resending a multi-MB payload because a neighbouring line moved would be
        wasteful, unlike plain text which is cheap to resend whole.
        """
        pending = [rw for rw in raw_writes if self._sent_raw.get((rw.x, rw.y)) != rw.token]
        if not pending:
            return

        out = ""
        hw_cursor = self._hw_cursor_row
        for rw in pending:
            row_delta = rw.y - hw_cursor
            if row_delta > 0:
                out += f"\x1b[{row_delta}B"
            elif row_delta < 0:
                out += f"\x1b[{-row_delta}A"
            out += f"\x1b[{rw.x + 1}G"
            out += rw.data
            hw_cursor = rw.y
            self._sent_raw[(rw.x, rw.y)] = rw.token
        self._terminal.write(out)
        self._hw_cursor_row = hw_cursor

    # -------------------------------------------------------------------------
    # Resize
    # -------------------------------------------------------------------------

    def _on_resize(self) -> None:
        if self._keep_diff_on_height_change():
            return
        # Clear state; the next render forces a full clear+redraw even if the
        # reported width didn't change (e.g. a height-only resize), so a stale
        # frame is never left on screen for the new render to stack atop.
        self._prev = None
        self._hw_cursor_row = 0
        self._viewport_top = 0
        self._resized = True

    def _keep_diff_on_height_change(self) -> bool:
        """Survive a Termux height-only resize without discarding diff state.

        Termux reports a height change every time the on-screen keyboard shows
        or hides, which on the normal path costs a full clear plus a replay of
        the entire transcript — on every keyboard toggle, which is most of what
        typing on Android is. Width is untouched, so nothing needs rewrapping
        and the diff stays valid; only the viewport anchor moves. Restricted to
        Termux because everywhere else a height change is a real window resize,
        where keeping a stale frame on screen is the worse failure.
        """
        if not _IS_TERMUX or self._prev is None or self._prev_width == 0:
            return False
        if self._terminal.width != self._prev_width:
            return False  # genuine reflow; needs the full redraw
        # Re-anchor the viewport: the terminal scrolled content itself when it
        # changed height, so the absolute row at screen row 0 moved even though
        # nothing was repainted. Derive it from content length the same way
        # _full_render does — content shorter than the screen pins to the top,
        # not the bottom.
        self._viewport_top = max(0, len(self._prev) - self._terminal.height)
        self._prev_height = self._terminal.height
        return True
