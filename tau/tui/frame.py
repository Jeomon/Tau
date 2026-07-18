"""Frame / BufferedTerminal: the double-buffered render loop.

This is the piece that ties the rest of the render layer together:
``BufferedTerminal.draw()`` hands a ``Frame`` to a callback, the callback
calls ``frame.render_widget()`` for each widget (writing into the frame's
``Buffer``), then the previous frame's ``Buffer`` is diffed against the new
one and only the changed cells are sent to the ``Backend``.

Named ``BufferedTerminal`` rather than ``Terminal`` to avoid colliding with
``tau.tui.terminal.Terminal`` (the raw termios/ANSI I/O wrapper this sits on
top of via ``AnsiBackend`` — see ``backend.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.backend import Backend
from tau.tui.buffer import _BLANK_CELL, Buffer, Cell
from tau.tui.geometry import Position, Rect
from tau.tui.style import OSC8_CLOSE, Style, style_transition
from tau.tui.utils import grapheme_width, is_window_focused
from tau.tui.widget import Widget

if TYPE_CHECKING:
    from tau.tui.terminal import Terminal


@dataclass(frozen=True, slots=True)
class Fullscreen:
    """The app owns the whole terminal (alt-screen). ``Frame.area`` always starts at (0, 0)."""


@dataclass(frozen=True, slots=True)
class Fixed:
    """A manually-managed region — never auto-resized, call ``BufferedTerminal.resize()``."""

    area: Rect


@dataclass(frozen=True, slots=True)
class Inline:
    """Renders ``height`` rows into the normal scrollback at the current cursor row.

    Everything above the viewport
    stays real terminal scrollback, matching what ``service.py``'s existing
    ``Renderer`` already does by hand for Tau's actual chat UI. Simplified
    The cursor row is fixed at construction rather than
    dynamically tracked, so it doesn't auto-scroll the terminal to keep the
    viewport visible as content grows — the caller is responsible for that.
    """

    height: int
    cursor_row: int = 0


Viewport = Fullscreen | Fixed | Inline


@dataclass(slots=True)
class Frame:
    """The per-draw-call handle widgets render into."""

    buffer: Buffer
    area: Rect
    cursor_position: Position | None = None

    def render_widget(self, widget: Widget, area: Rect | None = None) -> None:
        widget.render(area if area is not None else self.area, self.buffer)

    def set_cursor_position(self, position: Position) -> None:
        self.cursor_position = position


class BufferedTerminal:
    """Own two ``Buffer`` objects and diff them each frame."""

    def __init__(self, backend: Backend, viewport: Viewport | None = None) -> None:
        self._backend = backend
        self._viewport: Viewport = viewport if viewport is not None else Fullscreen()
        area = self._compute_area(backend.size())
        self._buffers = [Buffer.empty(area), Buffer.empty(area)]
        self._current = 0

    @property
    def area(self) -> Rect:
        return self._buffers[self._current].area

    def resize(self, area: Rect) -> None:
        """Manually update a ``Fixed`` viewport's region (never resized automatically)."""
        self._viewport = Fixed(area)
        self._buffers = [Buffer.empty(area), Buffer.empty(area)]
        self._current = 0

    def _compute_area(self, terminal_size: Rect) -> Rect:
        if isinstance(self._viewport, Fixed):
            return self._viewport.area
        if isinstance(self._viewport, Inline):
            height = min(self._viewport.height, terminal_size.height)
            y = min(self._viewport.cursor_row, max(0, terminal_size.height - height))
            return Rect(0, y, terminal_size.width, height)
        return terminal_size

    def _resize_if_needed(self) -> None:
        if isinstance(self._viewport, Fixed):
            return  # Fixed viewports are not automatically resized.
        size = self._compute_area(self._backend.size())
        if size != self._buffers[self._current].area:
            self._buffers = [Buffer.empty(size), Buffer.empty(size)]
            self._current = 0

    def draw(self, render_fn: Callable[[Frame], None]) -> Buffer:
        """Render one frame and flush only the cells that changed to the backend."""
        self._resize_if_needed()
        current = self._buffers[self._current]
        current.content[:] = [_BLANK_CELL] * len(current.content)

        frame = Frame(current, current.area)
        render_fn(frame)

        previous = self._buffers[1 - self._current]
        updates = previous.diff(current)
        if updates:
            self._backend.draw(updates)
        if frame.cursor_position is not None:
            self._backend.set_cursor_position(frame.cursor_position)
        self._backend.flush()

        self._current = 1 - self._current
        return current


# ── ScrollbackTerminal ───────────────────────────────────────────────────────
#
# BufferedTerminal/Inline uses a fixed-size
# viewport, addressed with absolute cursor moves via Backend.draw(). That
# model cannot represent Tau's actual live UI — chat content grows without
# bound and old rows scroll into the terminal's real scrollback history,
# where they can never be addressed again (CSI ...H addresses the visible
# screen, not scrollback). So repainting here uses only relative moves
# (cursor up/down, \r\n to scroll) and talks to Terminal directly rather
# than through the absolute-addressing Backend protocol.
#
# The diff/paint algorithm below is a mechanical, behavior-preserving port of
# service.py's original string-based Renderer onto real Buffer/Cell rows — same
# viewport tracking, same relative-move + row-redraw strategy, same IME
# cursor handling — just sourced from Cell objects instead of re-parsing
# ANSI strings for every frame.


def _row_get(buf: Buffer, x: int, y: int) -> Cell:
    """Read a cell, treating anything outside the buffer's current rows as blank.

    Mirrors ``prev[i] if i < len(prev) else ""`` in the original string
    Renderer — a buffer that hasn't grown to a given row yet reads as blank
    there, rather than raising.
    """
    if y < 0 or y >= buf.area.height or x < 0 or x >= buf.area.width:
        return Cell()
    return buf.get(buf.area.x + x, buf.area.y + y)


def _row_equal(prev: Buffer, cur: Buffer, y: int, width: int) -> bool:
    for x in range(width):
        cur_cell = _row_get(cur, x, y)
        if cur_cell.skip:
            continue  # terminal owns these pixels; never a text-cell change
        if _row_get(prev, x, y) != cur_cell:
            return False
    return True


def _row_equal_at(prev: Buffer, y_prev: int, cur: Buffer, y_cur: int, width: int) -> bool:
    """Like ``_row_equal`` but compares two independently-indexed rows.

    Used to detect a pure insertion/deletion above the viewport (see
    ``ScrollbackTerminal._render``): the same visible text may now sit at a
    different absolute row index in ``cur`` than it did in ``prev``.
    """
    for x in range(width):
        cur_cell = _row_get(cur, x, y_cur)
        if cur_cell.skip:
            continue  # terminal owns these pixels; never a text-cell change
        if _row_get(prev, x, y_prev) != cur_cell:
            return False
    return True


def _diff_row_cells(prev: Buffer, cur: Buffer, y: int, width: int) -> str:
    """Compute a cell-buffer diff for one terminal row.

    Emits the minimal escapes to repaint just the cells that changed: an
    absolute column move only when the next write isn't immediately adjacent
    to the previous one, and SGR codes only when a cell's style differs from
    the previously written cell's. Continuation cells (the second column of
    a double-width glyph) are tracked by width rather than by checking for
    an empty symbol, since ``Cell.set_symbol`` coerces an empty placeholder
    to ``" "`` (see ``ansi_bridge.row_to_ansi`` for the same technique).
    """
    out: list[str] = []
    expected_col = -1
    active_style = Style()
    skip_cols = 0
    for col in range(width):
        if skip_cols > 0:
            skip_cols -= 1
            continue
        cell = _row_get(cur, col, y)
        if cell.skip:
            continue  # terminal owns these pixels; never repaint as text
        glyph_width = grapheme_width(cell.symbol) if cell.symbol else 1
        if cell == _row_get(prev, col, y):
            skip_cols = max(glyph_width - 1, 0)
            continue
        if col != expected_col:
            out.append(f"\x1b[{col + 1}G")
        if cell.style != active_style:
            out.append(style_transition(active_style, cell.style))
            active_style = cell.style
        out.append(cell.symbol or " ")
        expected_col = col + glyph_width
        skip_cols = max(glyph_width - 1, 0)
    if active_style.link:
        out.append(OSC8_CLOSE)
    if active_style != Style():
        out.append("\x1b[0m")
    return "".join(out)


class ScrollbackTerminal:
    """Differential renderer for unbounded, growing scrollback content.

    Renders into the main terminal buffer — no alternate screen. Content
    grows downward; old rows scroll into the terminal's native scrollback
    so the user can scroll back with the terminal's own scrollbar.
    Positioning uses only relative cursor moves so the terminal's own scroll
    state is never disrupted.

    ``render(buf)`` takes a full-width ``Buffer`` (``buf.area.width`` must
    equal ``terminal.width``; the caller is responsible for left/right
    margins and for compositing overlays into ``buf`` before calling this —
    the diff engine here just paints whatever buffer it's given). Text
    cursor position comes from ``buf.cursor_position``.
    """

    def __init__(self, terminal: Terminal, show_hardware_cursor: bool = False) -> None:
        self._terminal = terminal
        self._show_hardware_cursor = show_hardware_cursor
        self._prev: Buffer | None = None
        self._hw_cursor_row: int = 0
        self._viewport_top: int = 0
        self._max_lines: int = 0
        self._prev_width: int = 0
        self._prev_height: int = 0
        self._resized: bool = False
        self._unsub_resize = terminal.on_resize(self._on_resize)
        self._disposed = False
        # Last-sent token per (x, y) anchor — see Buffer.raw_writes/RawWrite.
        # Cleared whenever the actual terminal screen gets erased (clear(),
        # or a resize/reset_with_clear's lazy full_render(clear=True)), since
        # a previously-drawn image no longer exists on screen at that point.
        self._sent_raw: dict[tuple[int, int], str] = {}

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def render(
        self,
        buf: Buffer,
        stable_through: int = 0,
        elided_range: tuple[int, int] | None = None,
    ) -> None:
        """Render ``buf`` differentially into the terminal scrollback buffer.

        ``stable_through``: the caller guarantees rows ``[0, stable_through)``
        are byte-identical to the buffer it passed on the *previous* call (the
        Renderer establishes this by splicing cached, never-rewritten Cell
        rows into the same absolute positions across frames — see
        ``MessageList.render_split_cells``). Skipping the comparison for that
        span turns the per-frame diff scan from O(total scrollback) into
        O(still-live content), which is what actually degrades over a long
        session — the vast majority of rows are finalized and provably
        unchanged, so there is nothing to gain by re-checking them.

        Callers may intentionally leave those rows blank in ``buf`` (see
        elision in ``TUI.render_cells``) to avoid re-copying a long frozen
        history.  This method reinstates them from the previous frame before
        diffing or committing ``_prev``, so blanks never paint or poison the
        next baseline.

        ``elided_range``: the exact ``[start, end)`` row span the caller left
        as untouched blank sentinels this frame (a subset of
        ``[0, stable_through)`` — the rest of that prefix, e.g. header/spacer
        rows, is re-rendered fresh every frame and must not be touched here).
        When given, reinstatement is a single slice copy instead of scanning
        every cell of every stable row to guess which ones are blank
        placeholders — that scan is itself O(stable_through * width), which
        defeats the whole point of eliding the prefix in the first place.
        """
        try:
            self._render(buf, stable_through, elided_range)
        finally:
            # Independent of whichever path above ran: a row that's entirely
            # skip=True cells (e.g. a brand-new image) never registers as
            # "changed" to the cell-diff loop (no non-skip cell ever
            # differs), so raw_writes need their own novelty check rather
            # than piggybacking on the text-cell diff outcome.
            self._flush_raw_writes(buf)

    def _render(
        self,
        buf: Buffer,
        stable_through: int = 0,
        elided_range: tuple[int, int] | None = None,
    ) -> None:
        width = self._terminal.width
        height = self._terminal.height
        width_changed = self._resized or (self._prev_width != 0 and self._prev_width != width)
        self._resized = False

        cursor_pos = buf.cursor_position
        new_rows = buf.area.height

        if self._prev is None and not width_changed:
            self._full_render(buf, cursor_pos, width, height, clear=False)
            return

        if width_changed:
            self._full_render(buf, cursor_pos, width, height, clear=True)
            return

        prev = self._prev
        assert prev is not None
        prev_rows = prev.area.height

        # Reinstate any cells the caller left blank for the stable prefix.
        # TUI.render_cells elides re-splicing already-stable MessageList rows
        # (they're already on screen and in ``prev``); without this copy those
        # holes would either paint as blanks or become the next frame's
        # baseline and erase history until a full redraw (e.g. resize).
        # Only fill rows that are still entirely blank sentinels — children
        # above MessageList (header, spacer) are re-rendered every frame and
        # must keep their fresh cells, even when they fall inside
        # ``stable_through`` for skip-diff purposes.
        if stable_through > 0 and prev.area.width == buf.area.width:
            if elided_range is not None:
                # Caller already knows the exact elided span — a single slice
                # copy, no per-cell scanning needed.
                est, eend = elided_range
                eend = min(eend, stable_through, prev.area.height, buf.area.height)
                if eend > est:
                    start = est * width
                    end = eend * width
                    buf.content[start:end] = prev.content[start:end]
            else:
                # Fallback for callers that only know the stable prefix, not
                # which rows within it were actually left blank — must scan
                # to tell re-rendered rows (e.g. header/spacer) apart from
                # elided placeholders.
                st = min(stable_through, prev.area.height, buf.area.height)
                for y in range(st):
                    start = y * width
                    end = start + width
                    row = buf.content[start:end]
                    if row and all(c is _BLANK_CELL for c in row):
                        buf.content[start:end] = prev.content[start:end]

        max_rows = max(new_rows, prev_rows)
        scan_start = max(0, min(stable_through, max_rows))
        first_changed = -1
        last_changed = -1
        for y in range(scan_start, max_rows):
            if not _row_equal(prev, buf, y, width):
                if first_changed == -1:
                    first_changed = y
                last_changed = y

        if first_changed == -1:
            # Still commit buf as the baseline so any non-scanned growth beyond
            # prev (shouldn't happen with first_changed == -1) and any
            # reinstated prefix stay aligned with what the terminal shows.
            self._prev = buf
            self._prev_width = width
            self._prev_height = height
            self._position_hw_cursor(cursor_pos, new_rows)
            return

        if first_changed < self._viewport_top:
            if new_rows == prev_rows:
                if last_changed < self._viewport_top:
                    self._prev = buf
                    self._prev_width = width
                    self._prev_height = height
                    self._position_hw_cursor(cursor_pos, new_rows)
                    return
                first_changed = self._viewport_top
            else:
                # A row-count change entirely above the viewport (e.g. expanding/
                # collapsing a tool-call detail block that has already scrolled
                # off-screen) can't be reflected with relative cursor moves in
                # general — but the common case is a *pure* insertion/deletion:
                # everything from the viewport down is byte-identical to before,
                # just shifted by ``delta`` rows. When that holds, the physical
                # screen is already correct pixel-for-pixel (a real terminal
                # can't retroactively edit rows that already scrolled into
                # native history anyway) — only our bookkeeping's row numbering
                # needs to shift. Skipping the write here avoids the visible
                # "scrollback jumps to bottom" snap on every such edit.
                delta = new_rows - prev_rows
                vt = self._viewport_top
                tail_len = prev_rows - vt
                shifted_vt = vt + delta
                if (
                    tail_len >= 0
                    and shifted_vt >= 0
                    and shifted_vt + tail_len <= new_rows
                    and buf.area.width == prev.area.width
                    and all(
                        _row_equal_at(prev, vt + i, buf, shifted_vt + i, width)
                        for i in range(tail_len)
                    )
                ):
                    self._hw_cursor_row += delta
                    self._viewport_top = shifted_vt
                    self._max_lines = max(0, self._max_lines + delta)
                    self._prev = buf
                    self._prev_width = width
                    self._prev_height = height
                    self._position_hw_cursor(cursor_pos, new_rows)
                    return
                self._full_render(buf, cursor_pos, width, height, clear=True)
                return

        # === Differential render ===
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
            if _row_equal(prev, buf, y, width):
                # Unchanged row inside a repainted span: still occupies a
                # row but its content is already correct on screen, so
                # redraw it as-is rather than clearing and rewriting.
                out += "\x1b[2K"
                if y < new_rows:
                    out += row_to_ansi(buf, buf.area.y + y, embed_raw=False)
                continue
            out += _diff_row_cells(prev, buf, y, width)

        # The physical cursor sits at ``hw_cursor``: ``render_end`` when the
        # repaint loop ran, but still ``first_changed`` when the only changed
        # rows were removed trailing ones (``render_end < first_changed``
        # leaves the loop empty), so ``render_end`` must not be assumed.
        final_cursor_row = hw_cursor

        if prev_rows > new_rows:
            if final_cursor_row < new_rows - 1:
                move_down = new_rows - 1 - final_cursor_row
                out += f"\x1b[{move_down}B"
                final_cursor_row = new_rows - 1
            if final_cursor_row >= new_rows:
                # Empty repaint loop: the cursor already sits on the first
                # *changed* removed row (any removed rows above it were blank
                # in ``prev``, or the scan would have flagged them) — clear it
                # in place before clearing the rows below it.
                out += "\r\x1b[2K"
            extra = prev_rows - 1 - final_cursor_row
            for _ in range(extra):
                out += "\r\n\x1b[2K"
            if extra > 0:
                out += f"\x1b[{extra}A"

        self._hw_cursor_row = final_cursor_row
        self._max_lines = max(self._max_lines, new_rows)
        self._viewport_top = max(viewport_top, final_cursor_row - height + 1)
        self._prev = buf
        self._prev_width = width
        self._prev_height = height

        out += self._hw_cursor_ansi(cursor_pos, new_rows)
        out += self._terminal.end_sync()
        self._terminal.write(out)

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

    def dispose(self) -> None:
        """Release terminal subscriptions and retained render state."""
        if self._disposed:
            return
        self._disposed = True
        self._unsub_resize()
        self._prev = None

    def reset_with_clear(self) -> None:
        """Force a full clear-and-redraw on the next frame.

        Unlike reset(), this sets _resized so the render takes the clear=True
        path — homing the cursor before writing — which is required when content
        that was painted at arbitrary screen rows (e.g. an overlay) must be
        erased without a terminal resize event.
        """
        self.reset()
        self._resized = True

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _full_render(
        self,
        buf: Buffer,
        cursor_pos: Position | None,
        width: int,
        height: int,
        *,
        clear: bool,
    ) -> None:
        rows = buf.area.height
        if clear:
            self._sent_raw.clear()  # screen erased; a drawn image no longer exists there
        out = self._terminal.begin_sync()
        if clear:
            out += "\x1b[2J\x1b[H\x1b[3J"  # clear screen + scrollback
        else:
            out += "\r"  # start from column 0 for first render
        for i in range(rows):
            if i > 0:
                out += "\r\n"
            out += "\x1b[2K"
            out += row_to_ansi(buf, buf.area.y + i, embed_raw=False)

        self._hw_cursor_row = max(0, rows - 1)
        self._max_lines = rows if clear else max(self._max_lines, rows)
        self._viewport_top = max(0, max(height, rows) - height)
        self._prev = buf
        self._prev_width = width
        self._prev_height = height

        out += self._hw_cursor_ansi(cursor_pos, rows)
        out += self._terminal.end_sync()
        self._terminal.write(out)

    def _position_hw_cursor(self, cursor_pos: Position | None, rows: int) -> None:
        """Move the hardware terminal cursor to the IME position and show/hide it.

        Standalone entry point for the early-return render paths (no-change,
        viewport-skip, pure scroll) where nothing else is written this frame —
        see ``_hw_cursor_ansi`` for the paths that must batch this into the
        same synchronized-output block as the main diff paint.
        """
        self._terminal.write_flush(self._hw_cursor_ansi(cursor_pos, rows))

    def _hw_cursor_ansi(self, cursor_pos: Position | None, rows: int) -> str:
        """Compute (without writing) the cursor move/show/hide sequence, updating _hw_cursor_row.

        Split out from ``_position_hw_cursor`` so the main diff-paint and
        full-render paths can append this to their own ``out`` string and
        emit it inside the *same* begin_sync/end_sync batch as the content
        write. Issuing it as a separate write after that batch already closed
        (the previous behavior) is harmless while focused — it only ever
        writes a redundant, invisible "hide an already-hidden cursor" code —
        but while unfocused it instead shows/moves the real hardware cursor
        on every single frame (including every spinner tick) as a second,
        un-batched paint, which visibly flickers against the terminal's own
        unfocused-cursor rendering.
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
        # Reveal the real hardware cursor when the window is unfocused: the
        # terminal draws it as a hollow outline, giving the native unfocused
        # cursor look. While focused we keep it hidden and draw our own block.
        if self._show_hardware_cursor or not is_window_focused():
            out += "\x1b[?25h"  # show cursor
        else:
            out += "\x1b[?25l"  # hide cursor (we draw our own block)
        self._hw_cursor_row = target_row
        return out

    def _flush_raw_writes(self, buf: Buffer) -> None:
        """Send any raw_writes whose token changed since last sent.

        Independent of the text-cell diff outcome above — see the comment in
        render(). Uses its own relative cursor moves (not begin_sync/end_sync
        batching with the main paint, since this runs after it in a
        `finally`, once the main write — if any — has already gone out).
        """
        pending = [rw for rw in buf.raw_writes if self._sent_raw.get((rw.x, rw.y)) != rw.token]
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
        self._position_hw_cursor(buf.cursor_position, buf.area.height)

    def _on_resize(self) -> None:
        # Clear state; next render() call forces a full clear+redraw, even if
        # the reported width didn't change (e.g. a height-only resize), so a
        # stale frame is never left on screen for the new render to stack atop.
        self._prev = None
        self._hw_cursor_row = 0
        self._viewport_top = 0
        self._resized = True
