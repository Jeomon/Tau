from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tau.tui.component import Component

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# OverlayOptions
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# OverlayHandle
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# OverlayEntry (internal)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CustomOptions (for Layout.custom())
# ---------------------------------------------------------------------------


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
