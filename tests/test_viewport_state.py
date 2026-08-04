"""ViewportState — scroll position for the app-owned viewport backend.

The subtle requirements are the ones about *not* moving the view: output
arriving while the user reads history must not drag them forward, and
over-scrolling must not inflate an anchor that then takes just as many
scroll-downs to unwind.
"""

from __future__ import annotations

import pytest

from tau.tui.viewport import WHEEL_ROWS_PER_NOTCH, ViewportState, apply_wheel


class TestFollowing:
    def test_starts_at_the_bottom_following_output(self) -> None:
        vp = ViewportState()
        assert vp.anchor == 0
        assert vp.following
        assert vp.at_bottom

    def test_scrolling_up_stops_following(self) -> None:
        vp = ViewportState()
        vp.scroll_up(5)
        assert vp.anchor == 5
        assert not vp.following
        assert not vp.at_bottom

    def test_scrolling_back_to_the_bottom_resumes_following(self) -> None:
        vp = ViewportState()
        vp.scroll_up(5)
        vp.scroll_down(5)
        assert vp.anchor == 0
        assert vp.following

    def test_partial_scroll_down_does_not_resume_following(self) -> None:
        vp = ViewportState()
        vp.scroll_up(10)
        vp.scroll_down(4)
        assert vp.anchor == 6
        assert not vp.following

    def test_scroll_to_bottom_resumes_following_from_anywhere(self) -> None:
        vp = ViewportState()
        vp.scroll_up(999)
        vp.scroll_to_bottom()
        assert vp.anchor == 0
        assert vp.following

    def test_scroll_down_never_goes_past_the_bottom(self) -> None:
        vp = ViewportState()
        vp.scroll_up(3)
        vp.scroll_down(100)
        assert vp.anchor == 0


class TestNewOutputDoesNotYankTheView:
    """The requirement that makes the anchor an offset rather than a constant."""

    def test_following_keeps_the_newest_output_on_screen(self) -> None:
        vp = ViewportState()
        vp.on_content_grew(20)
        assert vp.anchor == 0
        assert vp.following

    def test_scrolled_up_view_stays_on_the_same_content(self) -> None:
        """Appended rows push what the user is reading further from the bottom,
        so the anchor must grow by the same amount or the view slides forward."""
        vp = ViewportState()
        vp.scroll_up(30)
        vp.on_content_grew(12)
        assert vp.anchor == 42
        assert not vp.following

    def test_repeated_output_while_reading_history_keeps_accumulating(self) -> None:
        vp = ViewportState()
        vp.scroll_up(10)
        for _ in range(5):
            vp.on_content_grew(3)
        assert vp.anchor == 25

    def test_zero_or_negative_growth_is_ignored(self) -> None:
        vp = ViewportState()
        vp.scroll_up(7)
        vp.on_content_grew(0)
        vp.on_content_grew(-4)
        assert vp.anchor == 7


class TestReconcile:
    """Clamping without ever forcing a full wrap."""

    def test_unknown_total_leaves_the_anchor_alone(self) -> None:
        """None means rows above exist but were never wrapped — no guessing."""
        vp = ViewportState()
        vp.scroll_up(500)
        vp.reconcile(None, height=40)
        assert vp.anchor == 500

    def test_anchor_is_clamped_to_the_top_when_the_total_is_known(self) -> None:
        vp = ViewportState()
        vp.scroll_up(10_000)
        vp.reconcile(known_total_rows=100, height=40)
        assert vp.anchor == 60  # 100 rows - a 40-row window

    def test_over_scrolling_does_not_need_unwinding(self) -> None:
        """The bug this exists to prevent: without clamping, scrolling up 10,000
        times would need 10,000 scroll-downs before the view moved at all."""
        vp = ViewportState()
        for _ in range(10_000):
            vp.scroll_up(1)
        vp.reconcile(known_total_rows=100, height=40)

        vp.scroll_down(1)

        assert vp.anchor == 59, "one scroll down must move the view by one row"

    def test_clamping_to_the_bottom_resumes_following(self) -> None:
        """A transcript shorter than the window has nowhere to scroll."""
        vp = ViewportState()
        vp.scroll_up(5)
        vp.reconcile(known_total_rows=20, height=40)
        assert vp.anchor == 0
        assert vp.following

    def test_reconcile_does_not_push_the_anchor_outward(self) -> None:
        vp = ViewportState()
        vp.scroll_up(5)
        vp.reconcile(known_total_rows=1000, height=40)
        assert vp.anchor == 5


class TestInputGuards:
    def test_non_positive_scrolls_are_no_ops(self) -> None:
        vp = ViewportState()
        vp.scroll_up(0)
        vp.scroll_up(-3)
        assert vp.anchor == 0
        assert vp.following, "a no-op scroll must not silently stop following"

        vp.scroll_up(5)
        vp.scroll_down(0)
        vp.scroll_down(-2)
        assert vp.anchor == 5


class TestWheelDecoding:
    """SGR wheel codes -> scroll actions.

    Modifier bits (shift 4, alt 8, ctrl 16) may be OR'd into the button code,
    and codes 66/67 are the *horizontal* wheel sharing the same wheel bit, so
    the code must be masked rather than compared.
    """

    def test_wheel_up_scrolls_back_through_history(self) -> None:
        vp = ViewportState()
        assert apply_wheel(vp, 64)
        assert vp.anchor == WHEEL_ROWS_PER_NOTCH
        assert not vp.following

    def test_wheel_down_scrolls_toward_new_output(self) -> None:
        vp = ViewportState()
        vp.scroll_up(10)
        assert apply_wheel(vp, 65)
        assert vp.anchor == 10 - WHEEL_ROWS_PER_NOTCH

    @pytest.mark.parametrize("modifier", [4, 8, 16, 4 | 16])
    def test_modifiers_do_not_break_direction(self, modifier: int) -> None:
        up = ViewportState()
        apply_wheel(up, 64 | modifier)
        assert up.anchor == WHEEL_ROWS_PER_NOTCH

        down = ViewportState()
        down.scroll_up(10)
        apply_wheel(down, 65 | modifier)
        assert down.anchor == 10 - WHEEL_ROWS_PER_NOTCH

    @pytest.mark.parametrize("horizontal", [66, 67])
    def test_horizontal_wheel_does_not_scroll_vertically(self, horizontal: int) -> None:
        """A trackpad swipe sideways must not jump the view up or down."""
        vp = ViewportState()
        assert not apply_wheel(vp, horizontal)
        assert vp.anchor == 0
        assert vp.following

    @pytest.mark.parametrize("button", [0, 1, 2, 3, 32, 35])
    def test_non_wheel_buttons_are_left_alone(self, button: int) -> None:
        """Clicks and drags are not this function's business."""
        vp = ViewportState()
        assert not apply_wheel(vp, button)
        assert vp.anchor == 0

    def test_notch_size_is_configurable(self) -> None:
        vp = ViewportState()
        apply_wheel(vp, 64, rows_per_notch=1)
        assert vp.anchor == 1
