from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable

from tau.tui.ansi import set_window_focused
from tau.tui.component import Component, Container, Focusable
from tau.tui.input import BgColorEvent, FocusEvent, InputEvent, KeyEvent
from tau.tui.overlay import OverlayEntry, OverlayHandle, OverlayOptions
from tau.tui.renderer import Renderer
from tau.tui.terminal import Terminal
from tau.tui.utils import project_name

_log = logging.getLogger(__name__)


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

    def __init__(self, show_hardware_cursor: bool = False) -> None:
        super().__init__()
        self._terminal = Terminal()
        self._renderer = Renderer(self._terminal, show_hardware_cursor=show_hardware_cursor)
        self._parser = _make_parser()

        self._running = False
        self._stop_event: asyncio.Event = asyncio.Event()
        self._last_render_at: float = 0.0
        self._render_timer: asyncio.TimerHandle | None = None
        self._render_requested = False
        self._esc_timer: asyncio.TimerHandle | None = None

        self._input_handlers: list[EventHandler] = []
        self._intercept_handlers: list[EventHandler] = []

        # Overlay stack — visible on top of base content
        self._overlays: list[OverlayEntry] = []
        self._focused_overlay: OverlayEntry | None = None

        # Explicit focus target for non-overlay components
        self._focused: Component | None = None

        # Terminal background color — populated after startup OSC 11 query
        self.background_color: tuple[int, int, int] | None = None
        self._bg_color_future: asyncio.Future | None = None

        # Wire resize → immediate full re-render (bypasses the streaming throttle)
        self._unsub_resize = self._terminal.on_resize(self._on_terminal_resize)

    # -------------------------------------------------------------------------
    # Container overrides — request render after structural changes
    # -------------------------------------------------------------------------

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
            self._terminal.set_title(f"τ - {project_name()}")
            self._terminal.hide_cursor()
            self._terminal.disable_autowrap()
            self._terminal.enable_bracketed_paste()
            self._terminal.enable_focus_reporting()
            self._renderer.reset()
            self._request_render()

            self._terminal.enable_kitty_keyboard()
            loop.add_reader(sys.stdin.fileno(), self._on_stdin_ready)
            # Fire-and-forget: query terminal background colour for theme hints
            asyncio.ensure_future(self.query_background_color()).add_done_callback(
                _log_task_exception
            )
            try:
                await self._stop_event.wait()
            finally:
                loop.remove_reader(sys.stdin.fileno())
                self._cancel_timers()
                self._terminal.disable_kitty_keyboard()
                self._terminal.disable_bracketed_paste()
                self._terminal.disable_focus_reporting()
                self._terminal.enable_autowrap()
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

    def request_render(self) -> None:
        """Ask for a render on the next frame (debounced). Call after state changes."""
        self._request_render()

    def on_input(self, handler: EventHandler) -> Callable[[], None]:
        """
        Register a global input handler. Returns an unsubscribe callable.

        The handler receives every InputEvent after focused components have
        had a chance to consume it. Handlers are called in registration order.
        """
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
            self._renderer.reset()
            self._request_render()

        def _set_hidden(hidden: bool) -> None:
            entry.hidden = hidden
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
        try:
            data = self._terminal.read_raw()
        except OSError:
            return

        if not data:
            return

        events = self._parser.feed(data)

        if self._parser._buf == "\x1b":
            self._schedule_esc_flush()

        for event in events:
            self._dispatch(event)

        if events:
            self._request_render()

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
           the cursor style; key-releases dropped.
        1. Intercept handlers — may consume before anyone else sees the event.
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

        # 0c. Key-release events (Kitty protocol) — drop to avoid double-firing.
        #     Code that explicitly needs releases can use on_input_intercept().
        if isinstance(event, KeyEvent) and event.released:
            return

        # 1. Intercept handlers
        for handler in self._intercept_handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result).add_done_callback(_log_task_exception)
            elif result is True:
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
        self._renderer.render(self, self._overlays or None)
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
