"""Scroll position for the app-owned viewport backend.

The native-scrollback backend has no state like this: the terminal owns the
scroll position, and the application cannot observe or set it. When the app
owns the viewport it must track the position itself — this is that state, kept
deliberately free of any rendering or terminal concerns so it can be reasoned
about (and tested) on its own.

The anchor is an offset **from the bottom**, not an absolute row. That choice is
what keeps the renderer's work proportional to the viewport: an absolute row
index would require knowing the total row count, which means wrapping the entire
transcript at the current width — the exact cost the app-viewport backend
exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ViewportState:
    """Where the viewport sits, and whether it tracks new output.

    ``anchor`` is how many rows above the newest row the window's bottom edge
    sits; 0 means the newest output is on screen. ``following`` means new output
    should keep the view pinned to the bottom.

    These are two facts, not one: a user who has scrolled up is *not* following,
    and new output must not yank them back — but they are also not necessarily
    at a fixed anchor, because appended rows push existing content upward.
    """

    anchor: int = 0
    following: bool = True

    def scroll_up(self, rows: int = 1) -> None:
        """Move the window back through history.

        Stops following: the user has expressed interest in a fixed position,
        so incoming output must not drag the view away from it.
        """
        if rows <= 0:
            return
        self.anchor += rows
        self.following = False

    def scroll_down(self, rows: int = 1) -> None:
        """Move the window toward the newest output.

        Arriving back at the bottom resumes following, which is what makes
        "scroll down to catch up" behave the way users expect without a
        separate command.
        """
        if rows <= 0:
            return
        self.anchor = max(0, self.anchor - rows)
        if self.anchor == 0:
            self.following = True

    def scroll_to_bottom(self) -> None:
        self.anchor = 0
        self.following = True

    def on_content_grew(self, rows: int) -> None:
        """Account for ``rows`` new rows appended at the bottom.

        While following, the anchor stays at 0 so the newest output stays on
        screen. While scrolled up, the anchor is *increased* by the same amount:
        the rows the user is reading have moved further from the bottom, and
        leaving the anchor alone would slide the view forward under them. This
        is the "do not yank the viewport" requirement, and it is why the anchor
        cannot simply be a fixed number the renderer reads.
        """
        if rows <= 0 or self.following:
            return
        self.anchor += rows

    def reconcile(self, known_total_rows: int | None, height: int) -> None:
        """Clamp the anchor once the true row count happens to be known.

        The renderer only reports a total when it exhausted history anyway, so
        this never forces a full wrap. Without it, scrolling up past the top
        would keep inflating the anchor, and the same number of scroll-downs
        would be needed before the view moved at all — a common and very
        confusing bug in hand-rolled scrollers.
        """
        if known_total_rows is None:
            return
        self.anchor = min(self.anchor, max(0, known_total_rows - max(0, height)))
        if self.anchor == 0:
            self.following = True

    @property
    def at_bottom(self) -> bool:
        return self.anchor == 0


# Bit 6 of an SGR mouse button code marks a wheel event; the low two bits then
# select the direction. Modifier bits (shift 4, alt 8, ctrl 16) may also be set,
# so the code must be masked rather than compared.
_WHEEL_BIT = 0b100_0000
_WHEEL_DIRECTION = 0b11
_WHEEL_UP = 0
_WHEEL_DOWN = 1

# Rows per wheel notch. Terminals send one event per notch and leave the
# distance to the application; three matches the usual terminal default.
WHEEL_ROWS_PER_NOTCH = 3


def apply_wheel(
    state: ViewportState,
    button: int,
    rows_per_notch: int = WHEEL_ROWS_PER_NOTCH,
) -> bool:
    """Scroll ``state`` for a wheel event. Returns whether it was consumed.

    Only vertical notches are handled. Codes 66/67 are the *horizontal* wheel —
    a trackpad's sideways swipe — and share bit 6 with vertical scrolling, so
    masking bit 0 alone would silently turn a sideways swipe into a vertical
    jump. They are reported as unconsumed so a caller can route them elsewhere.

    Returning a bool rather than swallowing everything matters: with mouse
    tracking on, the application receives *all* mouse input, including clicks it
    has no use for, and anything it does not handle should stay available to
    whatever does.
    """
    if not button & _WHEEL_BIT:
        return False
    direction = button & _WHEEL_DIRECTION
    if direction == _WHEEL_UP:
        state.scroll_up(rows_per_notch)
        return True
    if direction == _WHEEL_DOWN:
        state.scroll_down(rows_per_notch)
        return True
    return False  # horizontal wheel
