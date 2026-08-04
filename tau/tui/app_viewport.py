"""The app-viewport render backend: draw only what fits, own the scrolling.

Composes a frame as three bands inside a region Tau owns outright::

    [ top chrome    ]  children rendered before the transcript (header, spacer)
    [ message window]  exactly the rows that fit — the whole point
    [ bottom chrome ]  spinner, status, editor zone

Only the middle band is scrollable, and it is produced by
``render_visible_window``, so a frame costs the same on turn 3 and turn 3,000.
The native-scrollback backend cannot do this: there, every row needs an index
that lines up with what the terminal already holds, so the total row count must
be known, so the entire transcript must be re-wrapped on every resize.

The transcript component is found by duck-typing on ``render_visible_window``
rather than by importing MessageList, matching how ``Renderer`` already locates
``render_split_cells`` — ``tau.tui`` must not depend on ``tau.modes``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tau.tui.ansi_bridge import parse_ansi_wrapped_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.fixed_region import paint as _paint_region
from tau.tui.fixed_region import release as _release_region
from tau.tui.fixed_region import reserve as _reserve_region
from tau.tui.geometry import Rect
from tau.tui.service import composite_overlays
from tau.tui.viewport import ViewportState, apply_wheel

if TYPE_CHECKING:
    from tau.tui.terminal import Terminal


@runtime_checkable
class _ScrollableTranscript(Protocol):
    """A component that can render just the rows a viewport shows."""

    def render_visible_window(self, width: int, height: int, scroll_rows: int = 0) -> Any: ...


def _render_chrome(component: Any, width: int) -> Buffer | None:
    """Render one non-transcript child into its own buffer.

    Kept as cells rather than strings so it can be blitted into the region
    without a parse round-trip, and so overlays composite over real cells.
    """
    buf = Buffer.empty(Rect(0, 0, max(1, width), 0))
    rows = component.render_cells(Rect(0, 0, max(1, width), 0), buf)
    if rows <= 0:
        return None
    buf.grow_to(rows)
    return buf


class AppViewportRenderer:
    """Owns a fixed region on the main screen and the scroll position in it."""

    def __init__(self, terminal: Terminal) -> None:
        self._terminal = terminal
        self.viewport = ViewportState()
        self._started = False
        self._region_height = 0
        self._last_transcript_rows = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Claim the region and start receiving wheel events.

        Capturing the mouse is what takes native scrolling and click-drag
        selection away from the terminal, so this must only ever run when the
        backend was explicitly asked for.
        """
        if self._started:
            return
        self._started = True
        self._region_height = max(1, self._terminal.height)
        self._terminal.enable_mouse_tracking()
        self._terminal.write_flush(_reserve_region(self._region_height))

    def stop(self) -> None:
        """Hand the mouse back and leave the last frame on screen.

        Unlike the alternate screen, nothing is wiped: the final frame stays as
        ordinary scrollback, which is the one thing this model keeps over
        alt-screen.
        """
        if not self._started:
            return
        self._started = False
        self._terminal.write_flush(_release_region(self._region_height))
        self._terminal.disable_mouse_tracking()

    # ── input ────────────────────────────────────────────────────────────────

    def handle_mouse(self, button: int) -> bool:
        """Route a wheel event to the scroll position; True if it was consumed."""
        return apply_wheel(self.viewport, button)

    # ── rendering ────────────────────────────────────────────────────────────

    def render(self, children: list[Any], overlays: list | None = None) -> None:
        """Compose and paint one frame, overlays included."""
        if not self._started:
            return
        width = max(1, self._terminal.width)
        height = max(1, self._terminal.height)
        if height != self._region_height:
            # The terminal resized. Re-claim at the new size; the transcript is
            # re-wrapped for the visible window only, so this stays cheap no
            # matter how long the session is.
            self._region_height = height
            self._terminal.write_flush(_reserve_region(height))

        index = self._transcript_index(children)
        region = Buffer.empty(Rect(0, 0, width, 0))
        region.grow_to(height)

        if index is None:
            # No scrollable transcript in the tree (a full-screen takeover
            # replaced it) — draw every child as chrome rather than nothing.
            y = 0
            for child in children:
                chrome = _render_chrome(child, width)
                if chrome is None:
                    continue
                y = self._blit(region, chrome, y, height)
        else:
            top = [c for c in (_render_chrome(x, width) for x in children[:index]) if c]
            bottom = [c for c in (_render_chrome(x, width) for x in children[index + 1 :]) if c]
            top_rows = sum(c.area.height for c in top)
            bottom_rows = sum(c.area.height for c in bottom)

            # Chrome is bounded but not guaranteed to fit; always leave at least
            # one row for the transcript so a tall editor cannot blank it.
            window_height = max(1, height - top_rows - bottom_rows)
            if top_rows + bottom_rows >= height:
                # Drop the *top* band first: the editor and status are where the
                # user is acting, so they outrank a header.
                top, top_rows = [], 0
                window_height = max(1, height - bottom_rows)

            transcript = children[index]
            window = transcript.render_visible_window(width, window_height, self.viewport.anchor)
            self._track_growth(window)
            self.viewport.reconcile(window.known_total_rows, window_height)

            y = 0
            for chrome in top:
                y = self._blit(region, chrome, y, height)
            for line in window.lines:
                if y >= height:
                    break
                y += parse_ansi_wrapped_into(region, 0, y, line, width)
            # Pin the bottom chrome to the bottom edge, whatever the transcript did.
            y = max(0, height - bottom_rows)
            for chrome in bottom:
                y = self._blit(region, chrome, y, height)

        # Overlays sit on top of everything, positioned against the window — for
        # this backend the buffer *is* the window, so the viewport starts at 0.
        if overlays:
            composite_overlays(region, overlays, width, height, 0)

        region.grow_to(height)
        self._paint([row_to_ansi(region, i, embed_raw=False) for i in range(height)], width, height)

    @staticmethod
    def _blit(region: Buffer, chrome: Buffer, y: int, height: int) -> int:
        """Copy a chrome buffer in at row ``y``, clipped to the region."""
        rows = min(chrome.area.height, max(0, height - y))
        if rows > 0:
            region.blit(chrome, 0, y, Rect(0, 0, chrome.area.width, rows))
        return y + chrome.area.height

    def _paint(self, lines: list[str], width: int, height: int) -> None:
        self._terminal.write_flush(
            self._terminal.begin_sync()
            + _paint_region(lines, height, width)
            + self._terminal.end_sync()
        )

    @staticmethod
    def _transcript_index(children: list[Any]) -> int | None:
        for i, child in enumerate(children):
            if isinstance(child, _ScrollableTranscript):
                return i
        return None

    def _track_growth(self, window: Any) -> None:
        """Keep a scrolled-back view pinned to the content it is showing.

        Only meaningful when the total is known; otherwise growth is inferred
        from nothing and would move the view for no reason.
        """
        total = getattr(window, "known_total_rows", None)
        if total is None:
            self._last_transcript_rows = 0
            return
        if self._last_transcript_rows and total > self._last_transcript_rows:
            self.viewport.on_content_grew(total - self._last_transcript_rows)
        self._last_transcript_rows = total
