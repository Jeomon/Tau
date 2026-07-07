from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from tau.tui.component import Component, Container, Focusable
from tau.tui.input import BgColorEvent, FocusEvent, InputEvent, KeyEvent, MouseEvent
from tau.tui.terminal import Terminal
from tau.tui.utils import set_window_focused

_log = logging.getLogger(__name__)

# The asyncio event loops on Windows can't watch a console handle with
# add_reader, so stdin is pumped from a background thread there instead.
_IS_WINDOWS = sys.platform == "win32"

if TYPE_CHECKING:
    pass


# ── Overlay types ─────────────────────────────────────────────────────────────

# A size value: absolute column/row count or a percentage string like "60%"
SizeValue = int | str

# All nine anchor positions
OverlayAnchor = Literal[
    "center",
    "top-left",
    "top-center",
    "top-right",
    "left-center",
    "right-center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]


def _parse_size(value: SizeValue, reference: int) -> int:
    """Resolve a SizeValue against a reference dimension."""
    if isinstance(value, str) and value.endswith("%"):
        return int(reference * float(value[:-1]) / 100.0)
    return int(value)


@dataclass
class OverlayOptions:
    """
    Positioning and sizing options for a floating overlay window.

    Positioning and sizing options for a floating overlay window with full anchor support,
    percentage sizes,
    min/max constraints, explicit row/col positioning, responsive visibility,
    and per-side margin control.

    Examples::

        # Centred dialog, 60% wide, max 80% tall
        OverlayOptions(width="60%", max_height="80%", anchor="center")

        # Right-side panel pinned to the bottom-right
        OverlayOptions(width=40, anchor="bottom-right", margin=1)

        # Responsive: hide when terminal is narrower than 80 cols
        OverlayOptions(visible=lambda w, h: w >= 80)

        # Explicit absolute position
        OverlayOptions(row=5, col=10, width=30)
    """

    # ── Size ─────────────────────────────────────────────────────────────────
    # Width of the overlay (columns). Defaults to "60%".
    width: SizeValue = "60%"
    # Explicit height (rows). When None, the overlay's natural render height is used.
    height: SizeValue | None = None
    # Lower bound on width after percentage resolution.
    min_width: int | None = None
    # Upper bound on width.
    max_width: SizeValue | None = None
    # Lower bound on height.
    min_height: int | None = None
    # Upper bound on height. Defaults to "80%" so very tall components stay on screen.
    max_height: SizeValue | None = "80%"

    # ── Position ─────────────────────────────────────────────────────────────
    # Named anchor point for automatic positioning.  Overridden by row/col.
    anchor: OverlayAnchor = "center"
    # Fine-tune position after anchor calculation (signed, in rows/cols).
    offset_x: int = 0
    offset_y: int = 0
    # Explicit row (0-indexed from top). Overrides anchor row calculation.
    row: SizeValue | None = None
    # Explicit col (0-indexed from left). Overrides anchor col calculation.
    col: SizeValue | None = None

    # ── Margin ───────────────────────────────────────────────────────────────
    # Minimum gap from each terminal edge.  Either a uniform int or a dict
    # with optional keys "top", "right", "bottom", "left".
    margin: int | dict[str, int] = 1

    # ── Behaviour ────────────────────────────────────────────────────────────
    # Called each render cycle with (term_width, term_height).
    # Return False to hide the overlay on small terminals.
    visible: Callable[[int, int], bool] | None = None
    # If True the overlay is painted but does NOT capture keyboard focus.
    non_capturing: bool = False

    # ── Margins helper ───────────────────────────────────────────────────────
    def _margins(self) -> tuple[int, int, int, int]:
        """Return (top, right, bottom, left) margin values."""
        m = self.margin
        if isinstance(m, int):
            return m, m, m, m
        return (
            m.get("top", 1),
            m.get("right", 1),
            m.get("bottom", 1),
            m.get("left", 1),
        )


class OverlayHandle:
    """
    Returned by TUI.show_overlay() — controls a live overlay.

    Overlay handle API::

        handle = tui.show_overlay(MyDialog(), opts)
        handle.set_hidden(True)   # temporarily hide
        handle.show()             # make visible again
        handle.focus()            # steal keyboard focus
        handle.unfocus()          # release focus back
        handle.close()            # permanently remove
    """

    def __init__(
        self,
        close_fn: Callable[[], None],
        set_hidden_fn: Callable[[bool], None],
        focus_fn: Callable[[], None],
        unfocus_fn: Callable[[Component | None], None],
        is_focused_fn: Callable[[], bool],
        is_hidden_fn: Callable[[], bool],
    ) -> None:
        self._close_fn = close_fn
        self._set_hidden_fn = set_hidden_fn
        self._focus_fn = focus_fn
        self._unfocus_fn = unfocus_fn
        self._is_focused_fn = is_focused_fn
        self._is_hidden_fn = is_hidden_fn
        self._closed = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Permanently remove this overlay from the screen."""
        if not self._closed:
            self._closed = True
            self._close_fn()

    # Alias — calls it hide() when it means close
    hide = close

    # ── Visibility ────────────────────────────────────────────────────────────

    def set_hidden(self, hidden: bool) -> None:
        """Temporarily hide (True) or show (False) without closing."""
        if not self._closed:
            self._set_hidden_fn(hidden)

    def show(self) -> None:
        """Make the overlay visible (undo a set_hidden(True))."""
        self.set_hidden(False)

    @property
    def hidden(self) -> bool:
        """True while the overlay is temporarily hidden."""
        return self._is_hidden_fn()

    # ── Focus ─────────────────────────────────────────────────────────────────

    def focus(self) -> None:
        """Give keyboard focus to this overlay's component."""
        if not self._closed:
            self._focus_fn()

    def unfocus(self, target: Component | None = None) -> None:
        """
        Release focus from this overlay.

        ``target`` optionally specifies which component should receive
        focus next; if None, TUI restores the previous focus target.
        """
        if not self._closed:
            self._unfocus_fn(target)

    def is_focused(self) -> bool:
        """True when this overlay's component currently holds keyboard focus."""
        return self._is_focused_fn()


@dataclass
class OverlayEntry:
    """Internal: one entry on the TUI overlay stack."""

    component: Component
    options: OverlayOptions = field(default_factory=OverlayOptions)
    hidden: bool = False
    pre_focus: Component | None = None  # focus target to restore when this overlay closes

    def is_visible(self, term_w: int, term_h: int) -> bool:
        """Return False if the responsive visible() callback hides this overlay."""
        if self.hidden:
            return False
        fn = self.options.visible
        return fn(term_w, term_h) if fn is not None else True

    def resolve_width(self, term_w: int) -> int:
        """Compute overlay width from options, applying min/max constraints."""
        opt = self.options
        mt, mr, mb, ml = opt._margins()
        h_margin = ml + mr

        w = _parse_size(opt.width, term_w)

        if opt.min_width is not None:
            w = max(w, opt.min_width)
        if opt.max_width is not None:
            w = min(w, _parse_size(opt.max_width, term_w))

        return max(10, min(w, term_w - h_margin))

    def resolve(
        self,
        term_w: int,
        term_h: int,
        natural_h: int,
    ) -> tuple[int, int, int, int]:
        """
        Return (width, height, row, col) — all 0-indexed.

        ``natural_h`` is the component's unconstrained render line count.
        """
        opt = self.options
        mt, mr, mb, ml = opt._margins()

        # ── Width ─────────────────────────────────────────────────────────
        width = self.resolve_width(term_w)

        # ── Height ────────────────────────────────────────────────────────
        height = _parse_size(opt.height, term_h) if opt.height is not None else natural_h

        if opt.min_height is not None:
            height = max(height, opt.min_height)
        if opt.max_height is not None:
            height = min(height, _parse_size(opt.max_height, term_h))

        # Clamp to what the terminal can fit accounting for margins
        max_h = max(3, term_h - mt - mb)
        height = min(height, max_h)

        # ── Position via anchor ───────────────────────────────────────────
        anchor = opt.anchor
        if anchor == "top-left":
            row = mt
            col = ml
        elif anchor == "top-center":
            row = mt
            col = max(ml, (term_w - width) // 2)
        elif anchor == "top-right":
            row = mt
            col = max(ml, term_w - width - mr)
        elif anchor == "left-center":
            row = max(mt, (term_h - height) // 2)
            col = ml
        elif anchor == "right-center":
            row = max(mt, (term_h - height) // 2)
            col = max(ml, term_w - width - mr)
        elif anchor == "bottom-left":
            row = max(mt, term_h - height - mb)
            col = ml
        elif anchor == "bottom-center":
            row = max(mt, term_h - height - mb)
            col = max(ml, (term_w - width) // 2)
        elif anchor == "bottom-right":
            row = max(mt, term_h - height - mb)
            col = max(ml, term_w - width - mr)
        else:  # "center" — default
            row = max(mt, (term_h - height) // 2)
            col = max(ml, (term_w - width) // 2)

        # ── Explicit row/col overrides anchor ─────────────────────────────
        if opt.row is not None:
            row = _parse_size(opt.row, term_h)
        if opt.col is not None:
            col = _parse_size(opt.col, term_w)

        # ── Fine-tune with offset ─────────────────────────────────────────
        row = max(0, min(row + opt.offset_y, term_h - height))
        col = max(0, min(col + opt.offset_x, term_w - width))

        return width, height, row, col


@dataclass
class CustomOptions:
    """
    Options for ``Layout.custom()`` — controls how the factory component
    is displayed.

    overlay=False (default) swaps the TUI root to the custom component
    for a full-screen takeover; when the done() callback fires the
    Layout is restored.

    overlay=True renders the component as a floating overlay on top of
    the existing layout, using overlay_options for positioning.
    """

    overlay: bool = False
    overlay_options: OverlayOptions = field(default_factory=OverlayOptions)
    # Called with the OverlayHandle immediately after the overlay is shown
    on_handle: Callable[[OverlayHandle], None] | None = None


# ── Renderer ──────────────────────────────────────────────────────────────────

from tau.tui.ansi_bridge import row_to_ansi  # noqa: E402
from tau.tui.buffer import Buffer, RawWrite  # noqa: E402
from tau.tui.frame import ScrollbackTerminal  # noqa: E402
from tau.tui.geometry import Rect  # noqa: E402

# Blank columns reserved on the left/right edges of the terminal so content
# never touches the window border.
_LEFT_PAD = 1
_RIGHT_PAD = 1


class Renderer:
    """
    Scrollback-mode differential renderer.

    A thin wrapper over ``ScrollbackTerminal`` (``frame.py``): builds one
    ``Buffer`` for the whole tree per frame via ``Component.render_cells``,
    composites overlays into it as a real Buffer blit, then hands the
    finished buffer to the diff engine. Lines still scroll into the
    terminal's native scrollback; ``ScrollbackTerminal`` owns that behavior.
    """

    def __init__(self, terminal: Terminal, show_hardware_cursor: bool = False) -> None:
        self._terminal = terminal
        self._engine = ScrollbackTerminal(terminal, show_hardware_cursor=show_hardware_cursor)
        # Whether the previous frame composited any overlay pixels into the base
        # buffer. See render(): overlay compositing happens after stable_through
        # is computed from the base content alone, so it can land on rows the
        # base content considers "frozen" — tracked here so we know to force a
        # full diff both while an overlay is up and on the frame it closes.
        self._had_overlays = False

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def render(self, component: Component, overlays: list | None = None) -> None:
        """Render component differentially into the terminal scrollback buffer."""
        width = self._terminal.width - _LEFT_PAD - _RIGHT_PAD
        height = self._terminal.height

        buf = Buffer.empty(Rect(0, 0, self._terminal.width, 0))
        rows = component.render_cells(Rect(_LEFT_PAD, 0, max(1, width), 0), buf)
        buf.grow_to(max(1, rows))  # always at least one row so index math stays valid

        has_overlays = bool(overlays)
        if has_overlays:
            self._composite_overlays(buf, overlays, width, height)

        # stable_through only reflects the base content's frozen span — it has
        # no notion of overlay pixels blitted on top afterward. Trusting it while
        # an overlay is showing (or on the frame it just closed) can make the
        # overlay's cells — including live cursor/selection updates — never
        # reach the real terminal if they land inside that "frozen" span.
        if has_overlays or self._had_overlays:
            stable_through = 0
        else:
            stable_through = getattr(component, "_stable_rows", 0)
        self._had_overlays = has_overlays

        self._engine.render(buf, stable_through=stable_through)

    def clear(self) -> None:
        """Erase the entire screen and scrollback buffer."""
        self._engine.clear()

    def reset(self) -> None:
        """Force a full re-render on the next frame without clearing the screen."""
        self._engine.reset()

    def dispose(self) -> None:
        """Release terminal subscriptions and retained render state."""
        self._engine.dispose()

    def reset_with_clear(self) -> None:
        """Force a full clear-and-redraw on the next frame.

        Unlike reset(), this sets _resized so the render takes the clear=True
        path — homing the cursor before writing — which is required when content
        that was painted at arbitrary screen rows (e.g. an overlay) must be
        erased without a terminal resize event.
        """
        self._engine.reset_with_clear()

    # -------------------------------------------------------------------------
    # Compatibility accessors (TUI reads these directly — see tui.py below)
    # -------------------------------------------------------------------------

    @property
    def _viewport_top(self) -> int:
        return self._engine._viewport_top

    @property
    def _hw_cursor_row(self) -> int:
        return self._engine._hw_cursor_row

    @property
    def _prev_lines(self) -> list[str]:
        prev = self._engine._prev
        if prev is None:
            return []
        return [row_to_ansi(prev, y) for y in range(prev.area.height)]

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _composite_overlays(self, buf: Buffer, overlays: list, width: int, height: int) -> None:
        """Blit every visible overlay directly into buf's cells (in place)."""
        viewport_start = max(0, buf.area.height - height)

        for entry in overlays:
            if not entry.is_visible(width, height):
                continue
            ov_w = max(1, entry.resolve_width(width))
            ov_buf = Buffer.empty(Rect(0, 0, ov_w, 0))
            natural_h = entry.component.render_cells(Rect(0, 0, ov_w, 0), ov_buf)
            _ov_w2, ov_h, ov_row, ov_col = entry.resolve(width, height, natural_h)
            ov_h = min(ov_h, natural_h)

            buf.grow_to(viewport_start + ov_row + ov_h)
            for y in range(ov_h):
                target_y = viewport_start + ov_row + y
                if target_y < 0:
                    continue
                for x in range(ov_w):
                    target_x = _LEFT_PAD + ov_col + x
                    if target_x < 0 or target_x >= buf.area.width:
                        continue
                    cell = ov_buf.get(x, y)
                    buf.set(target_x, target_y, cell.symbol, cell.style)


# ── TUI ───────────────────────────────────────────────────────────────────────


def _log_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled() and (exc := task.exception()):
        _log.error("Unhandled exception in background task", exc_info=exc)


# Minimum milliseconds between rendered frames (~60 fps)
_MIN_RENDER_INTERVAL = 1 / 60

# How long to wait after a bare ESC before treating it as the Escape key
# rather than the start of an escape sequence (seconds)
_ESC_FLUSH_DELAY = 0.05


EventHandler = Callable[[InputEvent], bool | None | Awaitable[None]]


class TUI(Container):
    """
    Main TUI loop — a true Container whose children define the layout.

    Ties together Terminal (raw I/O), InputParser (key/mouse/paste events),
    and Renderer (differential scrollback rendering) into a single async loop.

    Content grows downward into the terminal's native scrollback buffer so
    the user can scroll back with the terminal's own scrollbar and select/copy
    text normally — no alternate screen, no custom scroll mode.

    Component API
    ---------
    * ``add_child`` / ``remove_child`` / ``clear`` — assemble the layout
      by inserting components in order (inherited from Container).
    * ``set_focus(component)`` — route keyboard input to any component;
      components implementing ``Focusable`` get their ``focused`` flag set.
    * ``set_title(title)`` — update the terminal window title bar.
    * ``show_overlay`` — floating overlay with a rich ``OverlayHandle``.

    Usage::

        tui = TUI()
        layout = Layout(tui, ...)   # layout adds itself via tui.add_child()
        tui.set_focus(layout)

        @tui.on_input
        def handle(event):
            if event.matches("ctrl+c"):
                tui.stop()

        await tui.run()
    """

    def __init__(
        self,
        show_hardware_cursor: bool = False,
        *,
        terminal: Terminal | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._terminal = terminal or Terminal()
        self._renderer = Renderer(self._terminal, show_hardware_cursor=show_hardware_cursor)
        self._parser = _make_parser()
        self._title = title

        self._running = False
        self._stop_event: asyncio.Event = asyncio.Event()
        self._last_render_at: float = 0.0
        self._render_timer: asyncio.TimerHandle | None = None
        self._render_requested = False
        self._esc_timer: asyncio.TimerHandle | None = None
        self._stdin_thread: threading.Thread | None = None

        self._input_handlers: list[EventHandler] = []
        self._intercept_handlers: list[EventHandler] = []

        # Overlay stack — visible on top of base content
        self._overlays: list[OverlayEntry] = []
        self._focused_overlay: OverlayEntry | None = None

        # Explicit focus target for non-overlay components
        self._focused: Component | None = None
        # Logical row occupied by each direct child during the latest render.
        # Mouse-aware children use this to translate terminal coordinates into
        # coordinates relative to their own rendered content.
        self._child_rows: dict[int, int] = {}
        # See render_cells: rows confirmed identical to last frame's buffer,
        # safe for ScrollbackTerminal to skip re-diffing.
        self._stable_rows: int = 0
        self._prev_stable_rows: int = 0
        # Last-seen child.frozen_generation, keyed by id(child) — lets
        # render_cells notice a child rebuilt its frozen cache (content changed
        # without necessarily changing row count) even between frames where
        # frozen_rows_this_frame happens to match _prev_stable_rows.
        self._child_frozen_gen: dict[int, int] = {}

        # Terminal background color — populated after startup OSC 11 query.
        # ``on_background_color`` (if set) fires once with the result (or None on
        # timeout); used for auto light/dark theme selection.
        self.background_color: tuple[int, int, int] | None = None
        # Optional background to set via OSC 11 on startup (CSS hex or "rgb(r,g,b)").
        self.terminal_bg: str | None = None
        self._bg_color_future: asyncio.Future | None = None
        self.on_background_color: Callable[[tuple[int, int, int] | None], None] | None = None
        self._disposed = False

        # Wire resize → immediate full re-render (bypasses the streaming throttle)
        self._unsub_resize = self._terminal.on_resize(self._on_terminal_resize)

    # -------------------------------------------------------------------------
    # Container overrides — request render after structural changes
    # -------------------------------------------------------------------------

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        """Render children into buf, recording their logical starting rows.

        Overrides Container's generic render_cells to also track
        _child_rows — without this override, _child_rows would stay empty,
        breaking mouse_position_for for every mouse-aware child (e.g. Layout).

        A child exposing ``render_split_cells`` (currently just MessageList)
        gets special-cased: its already-finalized rows are spliced in by
        reference from its own cache instead of being re-parsed every frame,
        and only its still-live tail goes through the normal per-frame path.
        ``self._stable_rows`` records how many of those spliced rows are also
        guaranteed identical to last frame's buffer (Renderer reads this to
        let ScrollbackTerminal skip re-diffing them).
        """
        from tau.tui.ansi_bridge import parse_ansi_wrapped_into

        y = area.y
        self._child_rows = {}
        frozen_rows_this_frame = 0
        frozen_content_changed = False
        for child in self.children:
            self._child_rows[id(child)] = y - area.y
            split = getattr(child, "render_split_cells", None)
            if split is not None:
                frozen_buf, live_lines = split(area.width)
                if frozen_buf is not None and frozen_buf.area.height:
                    frozen_rows = frozen_buf.area.height
                    buf.grow_to(y + frozen_rows)
                    fw = frozen_buf.area.width
                    for r in range(frozen_rows):
                        src = r * fw
                        dst = (y + r) * buf.area.width + area.x
                        buf.content[dst : dst + fw] = frozen_buf.content[src : src + fw]
                    if frozen_buf.raw_writes:
                        buf.raw_writes.extend(
                            RawWrite(area.x + rw.x, y + rw.y, rw.data, rw.token)
                            for rw in frozen_buf.raw_writes
                        )
                    frozen_rows_this_frame = frozen_rows
                    y += frozen_rows
                gen = getattr(child, "frozen_generation", None)
                if gen is not None and self._child_frozen_gen.get(id(child)) != gen:
                    # The frozen cache was rebuilt since we last saw this child (e.g.
                    # a theme/prefix change) — row count may be unchanged while the
                    # actual cell content differs, which a row-count-only comparison
                    # below can't detect. Force one full re-diff of the frozen span
                    # this frame so the renderer can't skip painting the change.
                    frozen_content_changed = True
                    self._child_frozen_gen[id(child)] = gen
                if live_lines:
                    for line in live_lines:
                        y += parse_ansi_wrapped_into(buf, area.x, y, line, area.width)
            else:
                y += child.render_cells(Rect(area.x, y, area.width, 0), buf)
        # Rows are only safe to skip re-diffing if they were ALSO the frozen
        # prefix last frame (same cached Cell objects both times) — a prefix
        # that just became frozen this frame may still differ from whatever
        # (different) content occupied those rows in last frame's buffer.
        self._stable_rows = (
            0 if frozen_content_changed else min(frozen_rows_this_frame, self._prev_stable_rows)
        )
        self._prev_stable_rows = frozen_rows_this_frame
        return y - area.y

    def mouse_position_for(self, component: Component, event: MouseEvent) -> tuple[int, int] | None:
        """Return a mouse event as zero-based coordinates relative to a direct child."""
        start = self._child_rows.get(id(component))
        if start is None:
            return None
        logical_row = self._renderer._viewport_top + event.y - 1
        # Mouse columns are one-based and the renderer reserves one left column.
        return logical_row - start, event.x - _LEFT_PAD - 1

    def add_child(self, component: Component) -> None:
        """Append a component to the layout and request a render."""
        super().add_child(component)
        self._request_render()

    def remove_child(self, component: Component) -> None:
        """Remove a component from the layout."""
        super().remove_child(component)
        self._renderer.reset()
        self._request_render()

    def clear(self) -> None:
        """Remove all children from the layout and erase what's on screen.

        Used for full-screen takeovers (e.g. TrustScreen) where the next
        render must fully replace the previous screen's content rather than
        being diffed/appended against it.
        """
        super().clear()
        self._renderer.clear()
        self._request_render()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def run(self) -> None:
        """Enter raw mode and run the event/render loop until stop() is called."""
        loop = asyncio.get_event_loop()
        self._running = True
        self._stop_event.clear()

        with self._terminal:
            if self._title is not None:
                self._terminal.set_title(self._title)
            self._terminal.hide_cursor()
            self._terminal.disable_autowrap()
            self._terminal.enable_bracketed_paste()
            self._terminal.enable_focus_reporting()
            if self.terminal_bg:
                self._terminal.set_background_color(self.terminal_bg)
            self._renderer.reset()
            self._request_render()

            self._terminal.enable_kitty_keyboard()
            if _IS_WINDOWS:
                # Windows event loops can't add_reader() a console handle, so a
                # daemon thread does the blocking read and hands each chunk back
                # to the loop thread.
                self._stdin_thread = threading.Thread(
                    target=self._win_stdin_loop,
                    args=(loop,),
                    name="tau-tui-stdin",
                    daemon=True,
                )
                self._stdin_thread.start()
            else:
                loop.add_reader(sys.stdin.fileno(), self._on_stdin_ready)

            # Query terminal background colour for theme hints, then notify any
            # listener (e.g. auto light/dark theme selection).
            async def _query_bg() -> None:
                color = await self.query_background_color()
                if self.on_background_color is not None:
                    self.on_background_color(color)

            asyncio.ensure_future(_query_bg()).add_done_callback(_log_task_exception)
            try:
                await self._stop_event.wait()
            finally:
                if _IS_WINDOWS:
                    # The daemon reader observes _stop_event and exits after its
                    # next read returns (or dies with the process); nothing to
                    # unregister from the loop.
                    self._stdin_thread = None
                else:
                    loop.remove_reader(sys.stdin.fileno())
                self._cancel_timers()
                self._terminal.disable_kitty_keyboard()
                self._terminal.disable_bracketed_paste()
                self._terminal.disable_focus_reporting()
                self._terminal.disable_mouse_tracking()
                self._terminal.enable_autowrap()
                if self.terminal_bg:
                    self._terminal.reset_background_color()
                # Move cursor past last rendered line so the shell prompt
                # appears below the TUI output (not on top of it).
                prev = self._renderer._prev_lines
                if prev:
                    hw = self._renderer._hw_cursor_row
                    last = len(prev) - 1
                    diff = last - hw
                    if diff > 0:
                        self._terminal.write(f"\x1b[{diff}B")
                    elif diff < 0:
                        self._terminal.write(f"\x1b[{-diff}A")
                self._terminal.write("\r\n")

    def stop(self) -> None:
        """Request the run loop to exit cleanly."""
        self._running = False
        self._stop_event.set()

    def dispose(self) -> None:
        """Release components, overlays, timers, handlers, and terminal callbacks."""
        if self._disposed:
            return
        self._disposed = True
        self._cancel_timers()
        for entry in self._overlays:
            entry.component.dispose()
        self._overlays.clear()
        for child in self.children:
            child.dispose()
        self.children.clear()
        self._focused = None
        self._focused_overlay = None
        self.on_background_color = None
        self._input_handlers.clear()
        self._intercept_handlers.clear()
        self._unsub_resize()
        self._renderer.dispose()

    def request_render(self) -> None:
        """Ask for a render on the next frame (debounced). Call after state changes."""
        self._request_render()

    def on_input(
        self,
        handler: EventHandler,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        """
        Register a global input handler. Returns an unsubscribe callable.

        The handler receives every InputEvent after focused components have
        had a chance to consume it. Handlers are called in registration order
        unless ``prepend`` places a higher-priority handler first. Returning
        ``True`` consumes the event.
        """
        if prepend:
            self._input_handlers.insert(0, handler)
        else:
            self._input_handlers.append(handler)
        return lambda: self._input_handlers.remove(handler)

    def on_input_intercept(self, handler: EventHandler) -> Callable[[], None]:
        """Register a pre-focused input interceptor. Returns an unsubscribe callable.

        Interceptors run before overlays and focused components. If a handler
        returns True the event is consumed — all other handlers are skipped.
        """
        self._intercept_handlers.append(handler)
        return lambda: self._intercept_handlers.remove(handler)

    # -------------------------------------------------------------------------
    # Focus management
    # -------------------------------------------------------------------------

    def set_focus(self, component: Component | None) -> None:
        """
        Route keyboard input to ``component`` exclusively.

        Components that implement ``Focusable`` have their ``focused``
        attribute updated automatically so they can adjust rendering
        (e.g. show/hide a text cursor).

        Pass ``None`` to clear explicit focus.
        """
        if isinstance(self._focused, Focusable):
            self._focused.focused = False  # type: ignore[union-attr]

        self._focused = component

        if isinstance(component, Focusable):
            component.focused = True  # type: ignore[union-attr]

    # -------------------------------------------------------------------------
    # Terminal title
    # -------------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        """Set the terminal window title bar text."""
        self._terminal.set_title(title)

    # -------------------------------------------------------------------------
    # Backward-compat root helpers
    # -------------------------------------------------------------------------

    @property
    def root(self) -> Component:
        """Return the first child (backward-compat accessor)."""
        return self.children[0] if self.children else self

    def set_root(self, component: Component) -> None:
        """
        Replace all children with a single component.

        Backward-compat shim used by TrustScreen and full-screen takeovers.
        Equivalent to ``clear(); add_child(component)``.
        """
        super().clear()
        super().add_child(component)
        self._renderer.reset()
        self._request_render()

    @property
    def terminal(self) -> Terminal:
        return self._terminal

    @property
    def renderer(self) -> Renderer:
        return self._renderer

    # -------------------------------------------------------------------------
    # Content notification — Layout calls this after adding messages
    # -------------------------------------------------------------------------

    def notify_content_added(self) -> None:
        """Request a render after new content is added (e.g. a new message)."""
        self._request_render()

    # -------------------------------------------------------------------------
    # Overlay management
    # -------------------------------------------------------------------------

    def show_overlay(
        self,
        component: Component,
        options: OverlayOptions | None = None,
    ) -> OverlayHandle:
        """
        Show a floating overlay window on top of the base content.

        Returns a rich ``OverlayHandle``::

            handle = tui.show_overlay(MyDialog(), opts)
            handle.set_hidden(True)    # temporarily hide
            handle.show()             # make visible again
            handle.focus()            # steal keyboard focus
            handle.unfocus()          # release focus back
            handle.close()            # permanently remove
        """
        entry = OverlayEntry(
            component=component,
            options=options or OverlayOptions(),
        )
        self._overlays.append(entry)
        if not (options and options.non_capturing):
            entry.pre_focus = self._focused
            self._focused_overlay = entry
            self.set_focus(component)
        self._request_render()

        # ── Handle callbacks ─────────────────────────────────────────────

        def _close() -> None:
            if entry in self._overlays:
                self._overlays.remove(entry)
            if self._focused_overlay is entry:
                capturing = [e for e in self._overlays if not e.options.non_capturing]
                if capturing:
                    self._focused_overlay = capturing[-1]
                    self.set_focus(capturing[-1].component)
                else:
                    self._focused_overlay = None
                    self.set_focus(entry.pre_focus)
            dispose = getattr(entry.component, "dispose", None)
            if callable(dispose):
                dispose()
            self._renderer.reset_with_clear()
            self._request_render(force=True)

        def _set_hidden(hidden: bool) -> None:
            if entry.hidden == hidden:
                return
            entry.hidden = hidden
            if hidden and self._focused_overlay is entry:
                capturing = [
                    overlay
                    for overlay in self._overlays
                    if overlay is not entry
                    and not overlay.hidden
                    and not overlay.options.non_capturing
                ]
                self._focused_overlay = capturing[-1] if capturing else None
                self.set_focus(
                    self._focused_overlay.component
                    if self._focused_overlay is not None
                    else entry.pre_focus
                )
            elif not hidden and not entry.options.non_capturing:
                entry.pre_focus = self._focused
                self._focused_overlay = entry
                self.set_focus(entry.component)
            self._request_render()

        def _focus() -> None:
            if entry in self._overlays:
                entry.pre_focus = self._focused
                self._focused_overlay = entry
                self.set_focus(entry.component)
                self._request_render()

        def _unfocus(target: Component | None) -> None:
            if self._focused_overlay is entry:
                self._focused_overlay = None
                restore = target if target is not None else entry.pre_focus
                self.set_focus(restore)
                self._request_render()

        def _is_focused() -> bool:
            return self._focused_overlay is entry

        def _is_hidden() -> bool:
            return entry.hidden

        return OverlayHandle(
            close_fn=_close,
            set_hidden_fn=_set_hidden,
            focus_fn=_focus,
            unfocus_fn=_unfocus,
            is_focused_fn=_is_focused,
            is_hidden_fn=_is_hidden,
        )

    # -------------------------------------------------------------------------
    # Terminal background colour query (OSC 11)
    # -------------------------------------------------------------------------

    async def query_background_color(self) -> tuple[int, int, int] | None:
        """Query the terminal for its background colour via OSC 11.

        Resolves to ``(r, g, b)`` each in 0–255, or ``None`` if the terminal
        doesn't reply within 500 ms.  The result is also stored in
        ``self.background_color`` for later access.

        Usage::

            color = await tui.query_background_color()
            if color and sum(color) < 384:
                apply_dark_theme()
        """
        loop = asyncio.get_event_loop()
        self._bg_color_future = loop.create_future()
        self._terminal.query_background_color()
        try:
            result = await asyncio.wait_for(asyncio.shield(self._bg_color_future), timeout=0.5)
            self.background_color = result
            return result
        except TimeoutError:
            return None
        finally:
            self._bg_color_future = None

    # -------------------------------------------------------------------------
    # Stdin reading
    # -------------------------------------------------------------------------

    def _on_stdin_ready(self) -> None:
        """Loop-thread callback (POSIX add_reader): read stdin and process it."""
        try:
            data = self._terminal.read_raw()
        except OSError:
            return
        self._process_input(data)

    def _process_input(self, data: str) -> None:
        """Feed raw input bytes through the parser and dispatch resulting events.

        Runs on the event-loop thread. On POSIX it is called directly from the
        add_reader callback; on Windows it is scheduled via call_soon_threadsafe
        from the stdin reader thread.
        """
        if not data:
            return

        events = self._parser.feed(data)

        if self._parser._buf == "\x1b":
            self._schedule_esc_flush()

        for event in events:
            self._dispatch(event)

        if events:
            self._request_render()

    def _win_stdin_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Windows stdin pump: blocking-read on a daemon thread.

        Reads keystrokes with the console in raw/VT mode (set by Terminal) and
        marshals each chunk onto the event loop via call_soon_threadsafe, since
        Windows event loops cannot watch a console handle with add_reader.
        """
        while not self._stop_event.is_set():
            try:
                data = self._terminal.read_raw()
            except OSError:
                break
            if not data:
                continue
            if self._stop_event.is_set():
                break
            try:
                loop.call_soon_threadsafe(self._process_input, data)
            except RuntimeError:
                break  # event loop already closed

    def _schedule_esc_flush(self) -> None:
        if self._esc_timer is not None:
            self._esc_timer.cancel()
        loop = asyncio.get_event_loop()
        self._esc_timer = loop.call_later(_ESC_FLUSH_DELAY, self._flush_esc)

    def _flush_esc(self) -> None:
        self._esc_timer = None
        events = self._parser.flush()
        for event in events:
            self._dispatch(event)
        if events:
            self._request_render()

    # -------------------------------------------------------------------------
    # Event dispatch
    # -------------------------------------------------------------------------

    def _dispatch(self, event: InputEvent) -> None:
        """
        Route an event through the handler chain.

        Priority (highest → lowest):
        0. System events — BgColorEvent stored silently; window focus toggles
           the cursor style.
        1. Intercept handlers — run for ALL events including key-releases so that
           handlers registered via on_input_intercept() can observe key-up events.
           Returning True consumes the event.
        0c. Key-release events (Kitty protocol) — dropped here so they never reach
            overlays, focused components, or global handlers.
        2. Focused overlay (if any) — modal; returning True blocks everything below.
           Visibility re-checked on each dispatch to handle terminal resize.
        3. Explicitly focused component (set_focus) — if no overlay has focus.
        4. Global input handlers — always run unless blocked by an overlay.
        """
        # 0a. Terminal background-colour response — store and stop routing.
        if isinstance(event, BgColorEvent):
            self.background_color = (event.r, event.g, event.b)
            if self._bg_color_future is not None and not self._bg_color_future.done():
                self._bg_color_future.set_result(self.background_color)
            return

        # 0b. Window focus in/out — toggle the cursor style and repaint.
        if isinstance(event, FocusEvent):
            set_window_focused(event.focused)
            self._request_render()
            return

        # 1. Intercept handlers — run before the release drop so handlers registered
        #    via on_input_intercept() can observe key-up events (Kitty protocol).
        for handler in self._intercept_handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result).add_done_callback(_log_task_exception)
            elif result is True:
                return

        # 0c. Key-release events (Kitty protocol) — drop after intercepts so they
        #     don't reach overlays, focused components, or global handlers.
        if isinstance(event, KeyEvent) and event.released:
            return

        # 2. Focused overlay (modal) — re-validate visibility first (terminal resize
        #    may have hidden it); redirect to the topmost still-visible overlay.
        if self._focused_overlay is not None:
            _w, _h = self._terminal.width, self._terminal.height
            if not self._focused_overlay.is_visible(_w, _h):
                _capturing = [
                    e
                    for e in self._overlays
                    if not e.options.non_capturing and e.is_visible(_w, _h)
                ]
                if _capturing:
                    self._focused_overlay = _capturing[-1]
                    self.set_focus(_capturing[-1].component)
                else:
                    restore = self._focused_overlay.pre_focus
                    self._focused_overlay = None
                    self.set_focus(restore)

        if self._focused_overlay is not None and not self._focused_overlay.hidden:
            consumed = self._focused_overlay.component.handle_input(event)
            if consumed:
                return

        # 3. Explicit focus target (non-overlay component)
        elif self._focused is not None:
            consumed = self._focused.handle_input(event)
            if consumed:
                return

        # 4. Global handlers
        for handler in self._input_handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result).add_done_callback(_log_task_exception)
            elif result is True:
                return

    # -------------------------------------------------------------------------
    # Render scheduling
    # -------------------------------------------------------------------------

    def _on_terminal_resize(self) -> None:
        """Repaint immediately on terminal resize.

        The terminal has already physically reflowed by the time this fires, so
        any throttled/coalesced paint would leave a stale or blank frame on
        screen (most visibly: the streaming spinner vanishing until the next
        token frame). Forcing the render here means resize never piggybacks on
        the rate-limited streaming loop. ``Renderer._on_resize`` runs first (it
        registers its callback during construction, before this one) so the
        renderer's full clear+redraw state is already set when we paint.
        """
        self._request_render(force=True)

    def _request_render(self, force: bool = False) -> None:
        """Coalesce render requests; always deferred to the event loop.

        ``force=True`` bypasses both the coalescer and the frame-rate throttle,
        cancelling any pending frame and painting synchronously on the spot —
        used for resize, where a delayed paint leaves the reflowed terminal
        showing stale content.
        """
        if force:
            if self._render_timer is not None:
                self._render_timer.cancel()
                self._render_timer = None
            self._render_requested = False
            self._do_render()
            return
        if self._render_requested:
            return
        self._render_requested = True
        elapsed = time.monotonic() - self._last_render_at
        delay = max(0.0, _MIN_RENDER_INTERVAL - elapsed)
        loop = asyncio.get_event_loop()
        self._render_timer = loop.call_later(delay, self._do_render)

    def _do_render(self) -> None:
        """Render all children into the scrollback buffer."""
        self._render_timer = None
        self._render_requested = False
        try:
            self._renderer.render(self, self._overlays or None)
        except Exception:
            # A single component raising during render must not permanently
            # freeze the UI. This callback runs via loop.call_later(), so an
            # unhandled exception is swallowed by asyncio's exception handler
            # and no further frames are painted — the screen appears stuck even
            # though the event loop (and the agent's coroutines) keep running.
            # Log the traceback and carry on so the next request_render() repaints.
            # NOTE: no extra args — exception() already captures exc_info; passing
            # the exception would make logging attempt "render failed" % (e,) and
            # raise a formatting error that ends up written to stderr.
            _log.exception("render failed")
        self._last_render_at = time.monotonic()

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def _cancel_timers(self) -> None:
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        self._render_requested = False
        if self._esc_timer is not None:
            self._esc_timer.cancel()
            self._esc_timer = None


# ---------------------------------------------------------------------------
# Module-level helper — keeps the import of InputParser out of the class body
# ---------------------------------------------------------------------------


def _make_parser():
    from tau.tui.input import InputParser

    return InputParser()
