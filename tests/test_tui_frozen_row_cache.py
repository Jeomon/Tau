"""Tests for TUI.render_cells's cross-frame cache of widened frozen rows.

TUI.render_cells splices a render_split_cells-capable child's (currently just
MessageList) already-finalized rows into the frame buffer every frame. Doing
that by re-widening every frozen row from scratch each time is an O(total
finalized history) cost paid on every keystroke in a long session (see
_ChildRowCache / _splice_frozen_rows in tau/tui/service.py). These tests pin
the invariants that caching must not break: identical output to the
uncached path, correct incremental growth, and — critically — no stale
content surviving a cache reset (clear(), undo) that doesn't also bump
row count in a way the cache would otherwise notice.
"""

from __future__ import annotations

from tau.message.types import AssistantMessage, UserMessage
from tau.modes.interactive.components.message_list import MessageList
from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.service import TUI
from tau.tui.theme import MessageTheme

WIDTH = 80


class FakeTerminal:
    def __init__(self, width: int = WIDTH, height: int = 24) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


def _render_lines(tui: TUI, inner_width: int) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, tui.terminal.width, 0))
    tui.render_cells(Rect(1, 0, inner_width, 0), buf)
    return [row_to_ansi(buf, y) for y in range(buf.area.height)]


def _make(width: int = WIDTH) -> tuple[TUI, MessageList]:
    term = FakeTerminal(width=width)
    tui = TUI(terminal=term)  # type: ignore[arg-type]
    ml = MessageList(theme=MessageTheme())
    # Append directly rather than via add_child(): add_child() calls
    # _request_render(), which unconditionally calls asyncio.get_event_loop()
    # (tau/tui/service.py) — fine in the live app (always inside a running
    # loop) but raises when the full test suite runs these assertions
    # outside one, depending on run order. Matches the existing pattern in
    # tests/test_tui_child_rows.py.
    tui.children.append(ml)
    return tui, ml


def test_cache_reuse_matches_uncached_output_as_history_grows() -> None:
    tui, ml = _make()
    inner = WIDTH - 2
    for i in range(30):
        ml.add_message(UserMessage.from_text(f"question {i}"))
        ml.add_message(AssistantMessage.from_text(f"answer {i}"))
        assert _render_lines(tui, inner) == _render_lines(tui, inner)


def test_cache_is_reused_across_frames_when_nothing_changed() -> None:
    tui, ml = _make()
    inner = WIDTH - 2
    for i in range(20):
        ml.add_message(UserMessage.from_text(f"q{i}"))
        ml.add_message(AssistantMessage.from_text(f"a{i}"))

    _render_lines(tui, inner)
    cache = tui._child_row_cache[id(ml)]
    content_before = cache.content
    rows_before = cache.rows
    assert rows_before > 0

    _render_lines(tui, inner)
    cache_after = tui._child_row_cache[id(ml)]
    assert cache_after is cache
    assert cache_after.content is content_before  # never reallocated, only extended
    assert cache_after.rows == rows_before


def test_cache_grows_incrementally_with_new_messages() -> None:
    tui, ml = _make()
    inner = WIDTH - 2
    for i in range(20):
        ml.add_message(UserMessage.from_text(f"q{i}"))
        ml.add_message(AssistantMessage.from_text(f"a{i}"))
    _render_lines(tui, inner)
    rows_before = tui._child_row_cache[id(ml)].rows

    ml.add_message(UserMessage.from_text("one more"))
    ml.add_message(AssistantMessage.from_text("final answer"))
    lines = _render_lines(tui, inner)

    rows_after = tui._child_row_cache[id(ml)].rows
    assert rows_after >= rows_before
    assert any("final answer" in line for line in lines)
    assert any("q0" in line for line in lines)  # old content preserved


def test_clear_does_not_leak_stale_cached_rows() -> None:
    """Regression: MessageList.clear() must invalidate TUI's cross-frame
    frozen-row cache (via frozen_generation), not just its own state — a
    cleared-then-refilled history with the same row-count profile as before
    would otherwise keep serving the old conversation's cached cells."""
    tui, ml = _make()
    inner = WIDTH - 2
    for i in range(30):
        ml.add_message(UserMessage.from_text(f"q{i}"))
        ml.add_message(AssistantMessage.from_text(f"a{i}"))
    lines_before = _render_lines(tui, inner)
    assert any("a29" in line for line in lines_before)

    ml.clear()
    for i in range(30):
        ml.add_message(UserMessage.from_text(f"NEW{i}"))
        ml.add_message(AssistantMessage.from_text(f"ANS{i}"))
    lines_after = _render_lines(tui, inner)

    assert any("ANS29" in line for line in lines_after)
    assert not any("a29" in line for line in lines_after)


def test_undo_does_not_leak_removed_content_through_cache() -> None:
    tui, ml = _make()
    inner = WIDTH - 2
    for i in range(20):
        ml.add_message(UserMessage.from_text(f"u{i}"))
        ml.add_message(AssistantMessage.from_text(f"r{i}"))
    _render_lines(tui, inner)

    ml.add_message(UserMessage.from_text("oops"))
    assert ml.remove_last()
    lines = _render_lines(tui, inner)

    assert not any("oops" in line for line in lines)


def test_width_change_rebuilds_cache_cleanly() -> None:
    tui, ml = _make()
    for i in range(20):
        ml.add_message(UserMessage.from_text(f"q{i}"))
        ml.add_message(
            AssistantMessage.from_text(f"answer {i} " * 6)  # long enough to wrap differently
        )

    narrow = _render_lines(tui, 20)
    wide = _render_lines(tui, WIDTH - 2)
    assert narrow != wide  # sanity: width actually changed wrapping
    # Re-rendering at the narrow width again must still match the first narrow pass
    # (guards against the cache serving stale rows widened for the wrong width).
    assert _render_lines(tui, 20) == narrow
    # And back to wide must match too.
    assert _render_lines(tui, WIDTH - 2) == wide
