"""Tests for tau/tui/frame.py — ScrollbackTerminal differential-render invariants.

Mirrors tests/test_tui_renderer.py's scenarios for the original string-based
Renderer, to confirm the Buffer/Cell port preserves the exact same
observable behavior (same writes, same viewport/cursor tracking) rather than
just "looking equivalent" in isolation.

Not ported: test_unchanged_lines_reuse_width_calculation. That test asserts
on Renderer's `_clamp_cache` (memoizing `visible_width` per line so
unchanged lines skip a wrap re-scan every frame). ScrollbackTerminal has no
such cache because it doesn't need one: components wrap their own content
once into Buffer rows at render_cells() time, so the engine never re-scans
a line's visible width on every frame the way the string Renderer did.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer
from tau.tui.component import StaticComponent
from tau.tui.frame import ScrollbackTerminal
from tau.tui.geometry import Rect


class FakeTerminal:
    """Minimal stand-in for tau.tui.terminal.Terminal, capturing writes."""

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []
        self.resize_callbacks: list[object] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        self.resize_callbacks.append(callback)

        def unsubscribe() -> None:
            self.resize_callbacks.remove(callback)

        return unsubscribe


def _content_writes(term: FakeTerminal) -> list[str]:
    """Writes from render()'s main buffer, with the trailing cursor-hide sequence
    stripped off the end (batched into the same write since cursor positioning
    was folded into the main sync block; these tests never set cursor_pos, so
    that sequence is always exactly "\\x1b[?25l") — and empty results dropped,
    matching the old behavior of filtering out a cursor-only write entirely.
    """
    result = []
    for w in term.writes:
        if w.endswith("\x1b[?25l"):
            w = w[: -len("\x1b[?25l")]
        if w:
            result.append(w)
    return result


def _buf(lines: list[str], width: int) -> Buffer:
    component = StaticComponent(lines)
    buf = Buffer.empty(Rect(0, 0, width, 0))
    component.render_cells(Rect(0, 0, width, 0), buf)
    return buf


class TestScrollbackTerminalDifferentialUpdate:
    def test_first_render_draws_everything(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "a" in content[0]
        assert "b" in content[0]
        assert "c" in content[0]

    def test_no_change_writes_no_content(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = ["a", "b", "c"]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        renderer.render(_buf(list(lines), term.width))

        assert _content_writes(term) == []

    def test_appended_line_only_redraws_new_line(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "b", "c", "d"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "d" in content[0]
        assert "a" not in content[0]
        assert "b" not in content[0]
        assert "c" not in content[0]

    def test_single_middle_line_change_only_redraws_that_line(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "X", "c"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "X" in content[0]
        assert "a" not in content[0]
        assert "c" not in content[0]

    def test_appended_suffix_only_rewrites_from_divergence_point(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["hello wor"], term.width))
        term.writes.clear()

        renderer.render(_buf(["hello world"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "ld" in content[0]
        assert "hello wor" not in content[0]
        assert "G" in content[0]
        assert "\x1b[2K" not in content[0]
        assert "\x1b[0K" not in content[0]

    def test_changed_cell_declares_its_style_and_resets_after(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["\x1b[31mred a"], term.width))
        term.writes.clear()

        renderer.render(_buf(["\x1b[31mred b"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "b" in content[0]
        assert "red" not in content[0]
        assert content[0].endswith("\x1b[0m")

    def test_mid_line_same_width_change_reuses_trailing_suffix(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["Loading | please wait"], term.width))
        term.writes.clear()

        renderer.render(_buf(["Loading / please wait"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "/" in content[0]
        assert "Loading" not in content[0]
        assert "please wait" not in content[0]
        assert "\x1b[0K" not in content[0]
        assert "\x1b[2K" not in content[0]

    def test_mid_line_insertion_only_rewrites_the_shifted_cells(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["hello world"], term.width))
        term.writes.clear()

        renderer.render(_buf(["hello there world"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "there" in content[0]
        assert "world" in content[0]
        assert "hello" not in content[0]
        assert "\x1b[2K" not in content[0]
        assert "\x1b[0K" not in content[0]

    def test_sparse_changes_redraw_full_span_including_unchanged_lines(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c", "d", "e"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "Z", "c", "d", "Y"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "Z" in content[0]
        assert "Y" in content[0]
        assert "c" in content[0]
        assert "d" in content[0]
        assert "a" not in content[0]

    def test_stable_height_offscreen_change_does_not_redraw(self):
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        changed = list(lines)
        changed[0] = "offscreen"
        renderer.render(_buf(changed, term.width))

        assert _content_writes(term) == []

    def test_stable_height_change_clamps_redraw_to_viewport(self):
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        changed = list(lines)
        changed[0] = "offscreen"
        changed[19] = "visible"
        renderer.render(_buf(changed, term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "v" in content[0]
        assert "sible" in content[0]
        assert "line 15" in content[0]
        assert "offscreen" not in content[0]
        assert "\x1b[2J" not in content[0]

    def test_elided_range_reinstates_only_the_given_span(self):
        """``elided_range`` must behave exactly like the scanning fallback:
        rows inside it (left as blank sentinels by the caller) are copied
        back from the previous frame; rows in ``[0, stable_through)`` but
        outside it (e.g. freshly re-rendered header/spacer rows) are left
        untouched even though they also fall within the stable prefix.
        """
        term = FakeTerminal(width=20, height=10)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = ["header", "spacer", "frozen 0", "frozen 1", "frozen 2"]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        # Next frame: header/spacer re-rendered fresh (identical text), the
        # "frozen" MessageList rows elided (left blank) since they're
        # unchanged and already on screen.
        buf2 = Buffer.empty(Rect(0, 0, term.width, 0))
        StaticComponent(["header", "spacer"]).render_cells(Rect(0, 0, term.width, 0), buf2)
        buf2.grow_to(5)

        renderer.render(buf2, stable_through=5, elided_range=(2, 5))

        assert _content_writes(term) == []
        assert renderer._prev is not None
        for y, expected in enumerate(lines):
            row = renderer._prev.content[y * term.width : y * term.width + len(expected)]
            assert "".join(c.symbol for c in row) == expected

    def test_elided_range_reinstate_is_bounded_to_the_given_span(self):
        """The reinstate copy must be confined to exactly ``elided_range``,
        not the whole ``stable_through`` prefix — rows outside it (e.g. a
        freshly re-rendered header) keep whatever ``buf`` already has, even
        if they fall inside ``[0, stable_through)``.
        """
        term = FakeTerminal(width=20, height=10)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = ["header", "spacer", "frozen 0", "frozen 1", "frozen 2"]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        buf2 = Buffer.empty(Rect(0, 0, term.width, 0))
        StaticComponent(["HEADER!", "spacer"]).render_cells(Rect(0, 0, term.width, 0), buf2)
        buf2.grow_to(5)

        renderer.render(buf2, stable_through=5, elided_range=(2, 5))

        assert renderer._prev is not None
        row0 = renderer._prev.content[0 : len("HEADER!")]
        assert "".join(c.symbol for c in row0) == "HEADER!"

    def test_offscreen_insertion_shifts_bookkeeping_without_redraw(self):
        """Row-count change entirely above the viewport (e.g. expanding a
        scrolled-off tool-call detail block via ctrl+o) must not blow away
        the terminal's native scrollback and snap the view to the bottom —
        the visible screen is already correct, only the row numbering shifted.
        """
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(_buf(lines, term.width))
        assert renderer._viewport_top == 15
        term.writes.clear()

        # Insert two new rows well above the viewport (viewport_top == 15);
        # everything from "line 15" on is untouched, just shifted down by 2.
        changed = lines[:2] + ["extra 1", "extra 2"] + lines[2:]
        renderer.render(_buf(changed, term.width))

        assert _content_writes(term) == []
        assert renderer._viewport_top == 17

    def test_offscreen_change_touching_visible_tail_falls_back_to_full_redraw(self):
        """If the insertion is paired with a real change to the visible
        content, the visible-region-unchanged shortcut can't apply and the
        renderer must still fall back to a full redraw to stay correct."""
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        lines = [f"line {i}" for i in range(20)]
        renderer.render(_buf(lines, term.width))
        term.writes.clear()

        changed = lines[:2] + ["extra 1", "extra 2"] + lines[2:]
        changed[-1] = "visible change"
        renderer.render(_buf(changed, term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "\x1b[2J" in content[0]

    def test_growing_buffer_across_frames(self):
        """A component whose row count grows (real chat history growth)."""
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "b", "c", "d", "e", "f"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "c" in content[0]
        assert "f" in content[0]
        assert "a" not in content[0]

    def test_resize_forces_full_redraw(self):
        term = FakeTerminal(width=20, height=5)
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b"], term.width))
        term.writes.clear()

        term.width = 30
        for cb in list(term.resize_callbacks):
            cb()
        renderer.render(_buf(["a", "b"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        assert "\x1b[2J" in content[0]
        assert "a" in content[0]
        assert "b" in content[0]

    def test_dispose_unsubscribes_resize_and_clears_state(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a"], term.width))
        assert term.resize_callbacks != []

        renderer.dispose()

        assert term.resize_callbacks == []
        assert renderer._prev is None


class TestTrailingShrink:
    """Content shrink where the identical prefix leaves the repaint loop empty.

    Regression: the trailing-clear arithmetic assumed the cursor sat at
    ``render_end`` (the last repainted row), but when the only changed rows
    are the removed trailing ones nothing is repainted and the cursor still
    sits on the first removed row — the old code then cleared rows one past
    the removed span (leaving the first removed row's content on screen) and
    recorded ``_hw_cursor_row`` one row above the physical cursor, so every
    later relative-move paint landed one row off.
    """

    def test_shrink_with_identical_prefix_clears_exactly_the_removed_rows(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c", "d", "e"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "b", "c"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        # Cursor starts at row 4 (bottom of the 5-row frame): up 1 to row 3
        # (the first removed row), clear it in place, then row 4 below it,
        # then back up — exactly the two removed rows erased, none repainted.
        assert content[0] == "\x1b[1A\r\r\x1b[2K\r\n\x1b[2K\x1b[1A"
        # Bookkeeping must match the physical cursor (row 3), not render_end.
        assert renderer._hw_cursor_row == 3

    def test_paint_after_shrink_lands_on_correct_row(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c", "d", "e"], term.width))
        renderer.render(_buf(["a", "b", "c"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "b", "X"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        # The physical cursor sits on row 3 after the shrink, so repainting
        # row 2 must move exactly one row up first.
        assert content[0].startswith("\x1b[1A\r")
        assert "X" in content[0]

    def test_shrink_with_mid_content_change_keeps_original_clear_sequence(self):
        term = FakeTerminal()
        renderer = ScrollbackTerminal(term)  # type: ignore[arg-type]
        renderer.render(_buf(["a", "b", "c", "d", "e"], term.width))
        term.writes.clear()

        renderer.render(_buf(["a", "Z", "c"], term.width))

        content = _content_writes(term)
        assert len(content) == 1
        # Repaint spans rows 1-2, then the two removed rows are cleared below
        # the cursor (row 2) and the cursor returns to row 2.
        assert "Z" in content[0]
        assert content[0].count("\x1b[2K") >= 2
        assert content[0].endswith("\x1b[2A")
        assert renderer._hw_cursor_row == 2
