"""MessageList.render_visible_window — the app-owned viewport renderer.

Two properties matter and are pinned here:

1. **Equivalence.** The lazy window must contain exactly the rows the existing
   full renderer would put on screen. If these ever diverge, the same transcript
   would look different depending on which backend drew it.
2. **Boundedness.** The work must be proportional to the viewport, not to
   session length. That is the entire reason this path exists, and it is not
   observable from the output alone — hence the ``units_rendered`` assertions.
"""

from __future__ import annotations

import pytest

from tau.message.types import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)
from tau.modes.interactive.components.message_list import MessageList
from tau.tui.utils import visible_width, wrap

HEIGHT = 40


def build(turns: int) -> MessageList:
    ml = MessageList(height=HEIGHT)
    for i in range(turns):
        ml.add_message(UserMessage(contents=[TextContent(content=f"question {i} " * 6)]))
        ml.add_message(
            AssistantMessage(
                contents=[TextContent(content=f"answer {i}. " + ("wordy prose here. " * 14))]
            )
        )
    return ml


def full_render_lines(ml: MessageList, width: int) -> list[str]:
    """The rows the existing (non-viewport) path lays out — the reference."""
    lines: list[str] = []
    for _start, _end, unit_lines in ml._iter_units(width):
        for line in unit_lines:
            lines.extend(wrap(line, width) if visible_width(line) > width else [line])
    return lines


def expected_window(ml: MessageList, width: int, height: int, scroll: int) -> list[str]:
    """Independently derived reference: wrap everything, then take the window.

    Over-scrolling parks at the top rather than sliding off the transcript, so
    the window stays full whenever there is enough content to fill it.
    """
    full = full_render_lines(ml, width)
    stop = max(min(height, len(full)), len(full) - scroll)
    return full[max(0, stop - height) : stop]


class TestEquivalenceWithTheFullRenderer:
    @pytest.mark.parametrize("scroll", [0, 1, 7, 33, 100, 150])
    def test_window_matches_the_full_render_at_every_offset(self, scroll: int) -> None:
        ml = build(60)
        got = ml.render_visible_window(90, HEIGHT, scroll)
        assert got.lines == expected_window(ml, 90, HEIGHT, scroll)

    @pytest.mark.parametrize("width", [40, 60, 90, 120, 200])
    def test_window_matches_after_a_width_change(self, width: int) -> None:
        """A resize re-wraps at a new width; the window must agree there too."""
        ml = build(40)
        ml.render_visible_window(100, HEIGHT)  # warm at the old width
        got = ml.render_visible_window(width, HEIGHT, scroll_rows=5)
        assert got.lines == expected_window(ml, width, HEIGHT, 5)

    def test_window_matches_with_tool_call_units(self) -> None:
        """Assistant+tool pairs render as one unit; backwards walking must not
        split them or the grouping would differ from the full renderer."""
        ml = MessageList(height=HEIGHT)
        for i in range(20):
            ml.add_message(UserMessage(contents=[TextContent(content=f"do thing {i}")]))
            ml.add_message(
                AssistantMessage(
                    contents=[ToolCallContent(id=f"c{i}", name="read", args={"path": "f.py"})]
                )
            )
            ml.add_message(
                ToolMessage(
                    contents=[
                        ToolResultContent(id=f"c{i}", tool_name="read", content=f"result {i}\n" * 3)
                    ]
                )
            )
        got = ml.render_visible_window(90, HEIGHT, scroll_rows=4)
        assert got.lines == expected_window(ml, 90, HEIGHT, 4)


class TestBoundedWork:
    """Cost must not grow with session length — the reason this path exists."""

    def test_units_rendered_does_not_grow_with_the_transcript(self) -> None:
        counts = []
        for turns in (50, 200, 800):
            ml = build(turns)
            counts.append(ml.render_visible_window(90, HEIGHT).units_rendered)
        assert len(set(counts)) == 1, f"work grew with session length: {counts}"

    def test_only_a_fraction_of_units_is_touched_on_a_long_session(self) -> None:
        ml = build(800)  # 1,600 messages
        got = ml.render_visible_window(90, HEIGHT)
        assert got.units_rendered < 40
        assert not got.reached_top

    def test_scrolling_further_back_costs_proportionally_more_not_everything(self) -> None:
        ml = build(800)
        near = ml.render_visible_window(90, HEIGHT, scroll_rows=0).units_rendered
        far = ml.render_visible_window(90, HEIGHT, scroll_rows=400).units_rendered
        assert far > near  # more history traversed
        assert far < 200  # ...but nowhere near the whole session


class TestEdgesAndAnchoring:
    def test_scroll_past_the_top_parks_at_the_first_row(self) -> None:
        """Over-scrolling must show the top of the transcript, not blank rows."""
        ml = build(12)
        full = full_render_lines(ml, 90)
        assert len(full) > HEIGHT, "fixture must be taller than the viewport"

        got = ml.render_visible_window(90, HEIGHT, scroll_rows=10_000)

        assert got.reached_top
        assert got.lines == full[:HEIGHT]
        assert len(got.lines) == HEIGHT, "the window must stay full at the top"

    def test_reached_top_is_true_once_every_unit_is_drawn(self) -> None:
        ml = build(2)
        got = ml.render_visible_window(90, 1000)
        assert got.reached_top

    def test_reached_top_is_false_while_history_remains(self) -> None:
        ml = build(400)
        assert not ml.render_visible_window(90, HEIGHT).reached_top

    def test_empty_transcript(self) -> None:
        ml = MessageList(height=HEIGHT)
        got = ml.render_visible_window(90, HEIGHT)
        assert got.lines == []
        assert got.units_rendered == 0
        assert got.reached_top

    def test_zero_height_renders_nothing(self) -> None:
        ml = build(10)
        assert ml.render_visible_window(90, 0).lines == []

    def test_window_never_exceeds_the_requested_height(self) -> None:
        ml = build(200)
        for scroll in (0, 5, 60):
            assert len(ml.render_visible_window(90, HEIGHT, scroll).lines) <= HEIGHT

    def test_transcript_shorter_than_the_viewport_returns_all_of_it(self) -> None:
        ml = build(1)
        full = full_render_lines(ml, 90)
        assert len(full) < HEIGHT, "fixture must be shorter than the viewport"

        assert ml.render_visible_window(90, HEIGHT).lines == full
        # ...and scrolling within a transcript that already fits changes nothing
        assert ml.render_visible_window(90, HEIGHT, scroll_rows=5).lines == full

    def test_negative_inputs_are_clamped(self) -> None:
        ml = build(5)
        assert ml.render_visible_window(90, -5).lines == []
        assert (
            ml.render_visible_window(90, HEIGHT, -3).lines
            == ml.render_visible_window(90, HEIGHT, 0).lines
        )


class TestUnitBoundsSharesTheGroupingRule:
    """_unit_bounds was split out of _iter_units; they must not drift apart."""

    def test_bounds_match_iter_units(self) -> None:
        ml = MessageList(height=HEIGHT)
        ml.add_message(UserMessage(contents=[TextContent(content="hi")]))
        ml.add_message(
            AssistantMessage(
                contents=[ToolCallContent(id="c1", name="read", args={"path": "f.py"})]
            )
        )
        ml.add_message(
            ToolMessage(contents=[ToolResultContent(id="c1", tool_name="read", content="data")])
        )
        ml.add_message(AssistantMessage(contents=[TextContent(content="done")]))

        from_iter = [(s, e) for s, e, _lines in ml._iter_units(90)]
        assert ml._unit_bounds() == from_iter

    def test_assistant_tool_pair_is_one_unit(self) -> None:
        ml = MessageList(height=HEIGHT)
        ml.add_message(
            AssistantMessage(
                contents=[ToolCallContent(id="c1", name="read", args={"path": "f.py"})]
            )
        )
        ml.add_message(
            ToolMessage(contents=[ToolResultContent(id="c1", tool_name="read", content="data")])
        )
        assert ml._unit_bounds() == [(0, 2)]


class TestFrameCostIsIndependentOfSessionLength:
    """The regression that unit counting alone did not catch.

    ``units_rendered`` stayed flat while a frame still walked every block in the
    session to enumerate unit boundaries — cheap-looking type checks that made
    a 10,000-message session ~20x slower per frame than a 100-message one.
    """

    def test_does_not_enumerate_every_unit_in_the_session(self, monkeypatch) -> None:
        ml = build(500)
        called = False

        def spy(*args, **kwargs):
            nonlocal called
            called = True
            return []

        monkeypatch.setattr(ml, "_unit_bounds", spy)
        ml.render_visible_window(90, HEIGHT)
        assert not called, "_unit_bounds() scans the whole session; the tail walk must not use it"

    def test_blocks_inspected_does_not_grow_with_the_transcript(self) -> None:
        inspected: dict[int, int] = {}
        for turns in (50, 400, 1600):
            ml = build(turns)
            count = 0
            original = ml._unit_bounds_ending_at

            def counting(index, _orig=original):
                nonlocal count
                count += 1
                return _orig(index)

            ml._unit_bounds_ending_at = counting  # type: ignore[method-assign]
            ml.render_visible_window(90, HEIGHT)
            inspected[turns] = count

        assert len(set(inspected.values())) == 1, f"work grew with session length: {inspected}"
