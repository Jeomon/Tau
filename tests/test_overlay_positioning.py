"""Overlay geometry — anchors, percentage row/col, margins, offsets.

Values are cross-checked against the reference implementation's
``resolveAnchorRow``/``resolveAnchorCol`` and its percentage handling, which
measure everything inside the margin box rather than the raw terminal.
"""

from __future__ import annotations

import pytest

from tau.tui.service import OverlayEntry, OverlayOptions

TERM_W, TERM_H = 80, 24


def _place(natural_h: int = 10, **options) -> tuple[int, int]:
    """Return the (row, col) an overlay lands at."""
    entry = OverlayEntry.__new__(OverlayEntry)
    entry.options = OverlayOptions(**options)
    _, _, row, col = entry.resolve(TERM_W, TERM_H, natural_h=natural_h)
    return row, col


class TestAnchors:
    @pytest.mark.parametrize(
        ("anchor", "expected"),
        [
            ("top-left", (1, 1)),
            ("top-center", (1, 30)),
            ("top-right", (1, 59)),
            ("left-center", (7, 1)),
            ("center", (7, 30)),
            ("right-center", (7, 59)),
            ("bottom-left", (13, 1)),
            ("bottom-center", (13, 30)),
            ("bottom-right", (13, 59)),
        ],
    )
    def test_all_nine_anchors(self, anchor, expected):
        assert _place(width=20, height=10, anchor=anchor) == expected


class TestMarginsBoundTheBox:
    def test_centring_happens_inside_asymmetric_margins(self):
        # 24 rows, 6 off the top, none off the bottom → an 18-row box, so a
        # 10-row overlay centres at 6 + (18-10)//2 = 10, not at (24-10)//2 = 7.
        margin = {"top": 6, "right": 1, "bottom": 0, "left": 1}
        assert _place(width=20, height=10, anchor="center", margin=margin)[0] == 10

    def test_symmetric_margins_are_unaffected(self):
        assert _place(width=20, height=10, anchor="center", margin=1) == (7, 30)
        assert _place(width=20, height=10, anchor="center", margin=4) == (7, 30)

    def test_an_offset_cannot_push_through_the_margin(self):
        # The margin is a hard minimum gap, so -5 stops at the margin, not 0.
        assert _place(width=20, height=10, anchor="top-left", margin=3, offset_y=-5)[0] == 3

    def test_offsets_apply_within_the_margins(self):
        assert _place(width=20, height=10, anchor="top-left", margin=1, offset_y=3)[0] == 4


class TestPercentagePosition:
    """A percentage is a fraction of the free space, so the overlay always
    lands fully on screen — "50%" is centred, not "top edge at mid-screen"."""

    @pytest.mark.parametrize(
        ("row", "expected"),
        [("0%", 0), ("25%", 3), ("50%", 7), ("75%", 10), ("100%", 14)],
    )
    def test_row_percentages_span_the_free_space(self, row, expected):
        assert _place(width=20, height=10, row=row, margin=0)[0] == expected

    def test_col_percentages_span_the_free_space(self):
        assert _place(width=20, col="0%", margin=0)[1] == 0
        assert _place(width=20, col="50%", margin=0)[1] == 30
        assert _place(width=20, col="100%", margin=0)[1] == 60

    def test_percentages_start_from_the_margin(self):
        assert _place(width=20, height=10, row="0%", margin=2)[0] == 2

    def test_an_absolute_row_is_used_as_given(self):
        assert _place(width=20, height=10, row=5, margin=0)[0] == 5


class TestClamping:
    def test_an_absolute_position_past_the_edge_is_pulled_back(self):
        assert _place(width=20, height=10, row=999, margin=1)[0] == TERM_H - 1 - 10

    def test_an_overlay_taller_than_the_box_starts_at_the_margin(self):
        assert _place(natural_h=100, width=20, margin=2)[0] == 2
