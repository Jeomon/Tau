"""Fixed-region painting on the main screen (app-viewport backend).

The property everything rests on: a repaint must leave the cursor exactly where
it started and must never emit a line feed on the region's last row. If either
breaks, the region drifts up the screen one row per frame and the terminal
fills with duplicated junk — a failure that is obvious in a live terminal and
invisible in a naive unit test, so it is simulated here instead.
"""

from __future__ import annotations

import re

import pytest

from tau.tui.fixed_region import paint, release, reserve

ROW_MOVE = re.compile(r"\x1b\[(\d+)([AB])")


def net_row_delta(sequence: str) -> int:
    """Net vertical cursor movement of an escape sequence, in rows.

    Counts line feeds as +1 and CUU/CUD (``ESC[nA`` / ``ESC[nB``) as -n/+n.
    """
    delta = sequence.count("\n")
    for count, direction in ROW_MOVE.findall(sequence):
        delta += int(count) if direction == "B" else -int(count)
    return delta


def rows_drawn(sequence: str) -> int:
    """How many rows the sequence clears, i.e. how many it draws."""
    return sequence.count("\x1b[2K")


class TestPaintDoesNotScroll:
    """The invariant that makes an alt-screen-free fixed region possible."""

    @pytest.mark.parametrize("height", [1, 2, 5, 24, 40])
    def test_repaint_returns_the_cursor_to_where_it_started(self, height: int) -> None:
        out = paint([f"line {i}" for i in range(height)], height, width=80)
        assert net_row_delta(out) == 0, "the region would drift by this many rows per frame"

    @pytest.mark.parametrize("height", [1, 2, 5, 40])
    def test_never_ends_with_a_line_feed(self, height: int) -> None:
        """A line feed on the last row scrolls the screen and eats the region."""
        out = paint(["x"] * height, height, width=80)
        assert not out.rstrip("\r").endswith("\n")

    def test_line_feeds_only_ever_advance_within_the_region(self) -> None:
        """height rows need exactly height-1 line feeds — one more would scroll."""
        for height in (1, 3, 10, 40):
            out = paint(["x"] * height, height, width=80)
            assert out.count("\n") == height - 1

    def test_repeated_repaints_do_not_accumulate_drift(self) -> None:
        total = "".join(paint([f"frame {i}"], 5, width=80) for i in range(100))
        assert net_row_delta(total) == 0


class TestPaintContents:
    def test_draws_exactly_height_rows(self) -> None:
        assert rows_drawn(paint(["a", "b"], 7, width=80)) == 7

    def test_rows_beyond_the_content_are_cleared_not_skipped(self) -> None:
        """Otherwise a shrinking transcript leaves stale text on screen."""
        out = paint(["only line"], 4, width=80)
        assert rows_drawn(out) == 4
        assert "only line" in out

    def test_every_row_is_cleared_before_drawing(self) -> None:
        out = paint(["a", "b", "c"], 3, width=80)
        for chunk in out.split("\r\n"):
            assert "\x1b[2K" in chunk

    def test_content_is_clipped_to_width_so_it_cannot_soft_wrap(self) -> None:
        """A row that overflows wraps, adding a row and misaligning the region."""
        out = paint(["y" * 200], 1, width=20)
        assert "y" * 20 in out
        assert "y" * 21 not in out

    def test_wide_characters_are_clipped_by_columns_not_code_points(self) -> None:
        out = paint(["日" * 30], 1, width=10)
        assert out.count("日") == 5  # 5 double-width glyphs == 10 columns

    def test_ansi_styling_survives_clipping(self) -> None:
        out = paint(["\x1b[1mbold text here\x1b[0m"], 1, width=8)
        assert "\x1b[1m" in out

    def test_empty_content_still_clears_the_region(self) -> None:
        out = paint([], 5, width=80)
        assert rows_drawn(out) == 5
        assert net_row_delta(out) == 0


class TestEdgeCases:
    def test_zero_height_draws_nothing(self) -> None:
        assert paint(["a"], 0, width=80) == ""

    def test_negative_height_is_treated_as_zero(self) -> None:
        assert paint(["a"], -3, width=80) == ""

    def test_single_row_region_needs_no_vertical_movement(self) -> None:
        out = paint(["only"], 1, width=80)
        assert "\n" not in out
        assert net_row_delta(out) == 0

    def test_more_lines_than_height_draws_only_what_fits(self) -> None:
        out = paint([f"line {i}" for i in range(50)], 3, width=80)
        assert rows_drawn(out) == 3
        assert "line 3" not in out


class TestReserveAndRelease:
    @pytest.mark.parametrize("height", [1, 2, 5, 40])
    def test_reserve_leaves_the_cursor_at_the_top_of_the_region(self, height: int) -> None:
        """It scrolls to make room, then comes back — net movement is zero."""
        assert net_row_delta(reserve(height)) == 0

    def test_reserve_makes_room_for_the_whole_region(self) -> None:
        """height rows need height-1 line feeds below the current one."""
        for height in (2, 5, 40):
            assert reserve(height).count("\n") == height - 1

    def test_reserve_of_one_row_needs_no_room(self) -> None:
        assert "\n" not in reserve(1)

    def test_release_moves_below_the_region(self) -> None:
        """Exiting must not leave the cursor inside the region, or the shell
        prompt would overwrite the final frame."""
        assert net_row_delta(release(5)) == 5

    def test_reserve_then_paint_is_a_stable_cycle(self) -> None:
        """The two must agree on where the cursor lives, or frame 2 is offset."""
        cycle = reserve(10) + "".join(paint(["x"], 10, width=80) for _ in range(5))
        assert net_row_delta(cycle) == 0
