"""render_split_cells must visually agree with the full native render.

MessageList caches "finalized" render units as Cell rows (render_split_cells)
so a long session doesn't re-parse and re-diff its entire history every
frame. These tests pin the one invariant that matters: whatever the cache
returns must produce the same styled cells as the compatibility render path —
across growth, streaming, undo, toggling, theme changes, and resize.
"""

from __future__ import annotations

from tau.message.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)
from tau.modes.interactive.components.message_list import MessageList
from tau.tui.ansi_bridge import parse_ansi_wrapped_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi, visible_width

WIDTH = 60


def _split_as_lines(ml: MessageList, width: int) -> list[str]:
    """Reconstruct render_split_cells's output as plain ANSI lines for comparison.

    ``row_to_ansi`` always emits a full-width row (trailing blank cells become
    trailing spaces); ``MessageBlock``'s own ``render()`` doesn't pad short
    lines out to the terminal width. Both paint identically on a real
    terminal (a blank cell is a blank cell), so lines are compared with
    trailing whitespace stripped — the only thing that must match exactly is
    the actual content and its styling.
    """
    frozen_buf, live_lines = ml.render_split_cells(width)
    lines: list[str] = []
    if frozen_buf is not None:
        for y in range(frozen_buf.area.height):
            lines.append(row_to_ansi(frozen_buf, y).rstrip())
    live_buf = Buffer.empty(Rect(0, 0, width, 0))
    live_row = 0
    for line in live_lines:
        live_row += parse_ansi_wrapped_into(live_buf, 0, live_row, line, width)
    lines.extend(row_to_ansi(live_buf, y).rstrip() for y in range(live_row))
    return lines


def _render_as_lines(ml: MessageList, width: int) -> list[str]:
    """Render the complete message list through its native component contract."""
    buf = Buffer.empty(Rect(0, 0, width, 0))
    row = ml.render_cells(Rect(0, 0, width, 0), buf)
    return [row_to_ansi(buf, y).rstrip() for y in range(row)]


def _viewport_as_lines(ml: MessageList, width: int, start: int, height: int) -> list[str]:
    view = ml.render_viewport_cells(width, start, height)
    return [row_to_ansi(view.buf, y).rstrip() for y in range(view.buf.area.height)]


def test_row_metadata_tracks_frozen_and_live_units() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 5)
    ml.add_message(UserMessage.from_text("stream please"))
    streaming = ml.add_message(AssistantMessage.from_text("partial answer"), streaming=True)

    ml.render_split_cells(WIDTH, collect_metadata=True)

    metadata = ml.row_metadata
    assert metadata
    assert sum(unit.row_count for unit in metadata) == len(_split_as_lines(ml, WIDTH))
    assert metadata[-1].start_block == len(ml._blocks) - 1
    assert metadata[-1].end_block == len(ml._blocks)
    assert metadata[-1].frozen is False
    assert any(unit.frozen for unit in metadata[:-1])

    streaming.set_streaming(False)
    streaming.finalize()
    streaming.invalidate()
    ml.render_split_cells(WIDTH, collect_metadata=True)
    assert all(unit.frozen for unit in ml.row_metadata)


def test_render_viewport_cells_matches_full_render_slices() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 20)
    ml.add_message(UserMessage.from_text("final question"))
    ml.add_message(AssistantMessage.from_text("final answer " * 20), streaming=True)

    full = _split_as_lines(ml, WIDTH)
    for start in (0, 3, 17, max(0, len(full) - 8), len(full) + 5):
        height = 7
        view = ml.render_viewport_cells(WIDTH, start, height)
        expected = full[start : start + height]
        assert view.total_rows == len(full)
        assert view.row_offset == max(0, start)
        assert _viewport_as_lines(ml, WIDTH, start, height) == expected


def test_render_viewport_cells_reflows_after_width_change() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 8)
    ml.add_message(AssistantMessage.from_text("wide words " * 30), streaming=True)

    narrow_full = _split_as_lines(ml, 32)
    wide_full = _split_as_lines(ml, WIDTH)
    assert narrow_full != wide_full

    assert _viewport_as_lines(ml, 32, 2, 10) == narrow_full[2:12]
    assert _viewport_as_lines(ml, WIDTH, 2, 10) == wide_full[2:12]
def _add_conversation(ml: MessageList, n: int) -> None:
    for i in range(n):
        ml.add_message(UserMessage.from_text(f"question number {i}"))
        ml.add_message(AssistantMessage.from_text(f"answer number {i} " * 3))


def test_split_matches_full_render_as_history_grows() -> None:
    ml = MessageList(theme=MessageTheme())
    for i in range(40):
        ml.add_message(UserMessage.from_text(f"question {i}"))
        ml.add_message(AssistantMessage.from_text(f"answer {i}"))
        assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_split_matches_full_render_with_tool_call_pairing() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 5)
    assistant = AssistantMessage(
        contents=[ToolCallContent(id="t1", name="grep", args={"pattern": "foo"})]
    )
    ml.add_message(assistant)
    tool_msg = ToolMessage(
        contents=[ToolResultContent(id="t1", tool_name="grep", content="match.py:1")]
    )
    ml.add_message(tool_msg)
    _add_conversation(ml, 5)

    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_split_matches_full_render_during_streaming() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 10)
    ml.add_message(UserMessage.from_text("one more question"))
    streaming_block = ml.add_message(AssistantMessage.from_text(""), streaming=True)

    for chunk in ["Hello", " there", ", how", " are you?"]:
        streaming_block._message = AssistantMessage.from_text(
            streaming_block.message.text_content() + chunk
        )
        streaming_block.invalidate()
        assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)

    streaming_block.set_streaming(False)
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_frozen_cache_survives_incremental_calls_without_rebuilding() -> None:
    """Cached frozen rows must be reused (never rebuilt) across calls."""
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 20)
    ml.render_split_cells(WIDTH)
    frozen_buf_before = ml._frozen_buf
    frozen_rows_before = frozen_buf_before.area.height if frozen_buf_before else 0
    assert frozen_rows_before > 0

    ml.add_message(UserMessage.from_text("new question"))
    ml.add_message(AssistantMessage.from_text("new answer"))
    ml.render_split_cells(WIDTH)

    # Same Buffer object, only grown — old rows are the *same* Cell objects.
    assert ml._frozen_buf is frozen_buf_before
    assert ml._frozen_buf.area.height >= frozen_rows_before
    for y in range(frozen_rows_before):
        assert ml._frozen_buf.get(0, y) is frozen_buf_before.get(0, y)


def test_undo_pops_only_the_live_tail() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 20)
    ml.render_split_cells(WIDTH)
    frozen_before = ml._frozen_block_count
    assert frozen_before > 0

    ml.add_message(UserMessage.from_text("oops"))
    assert ml.remove_last()

    assert ml._frozen_block_count == frozen_before
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_toggle_details_expanded_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    for i in range(15):
        ml.add_message(UserMessage.from_text(f"q{i}"))
        long_thinking = "\n".join(f"thought {j}" for j in range(8))
        ml.add_message(
            AssistantMessage(
                contents=[ThinkingContent(content=long_thinking), TextContent(content=f"a{i}")]
            )
        )
    before = _render_as_lines(ml, WIDTH)
    ml.render_split_cells(WIDTH)  # populate the frozen cache

    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before  # sanity: toggling actually changed output
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_set_theme_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_cells(WIDTH)

    from tau.tui.style import Style

    new_theme = MessageTheme(you_label=Style())
    ml.set_theme(new_theme)

    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_width_change_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_cells(WIDTH)

    narrower = WIDTH - 10
    assert _split_as_lines(ml, narrower) == _render_as_lines(ml, narrower)


def test_long_tool_error_wraps_without_losing_content_and_reflows_on_resize() -> None:
    ml = MessageList(theme=MessageTheme())
    content = "request-failed-" + ("x" * 80) + "-tail"
    block = ml.add_message(
        ToolMessage(
            contents=[
                ToolResultContent(
                    id="tool-1",
                    tool_name="web_fetch",
                    content=content,
                    is_error=True,
                )
            ]
        )
    )
    block.finalize()

    narrow = _split_as_lines(ml, 24)
    wide = _split_as_lines(ml, 48)

    assert len(narrow) > len(wide) > 1
    assert all(visible_width(line) <= 24 for line in narrow)
    assert all(visible_width(line) <= 48 for line in wide)
    assert content in "".join(strip_ansi(line).strip() for line in narrow)
    assert content in "".join(strip_ansi(line).strip() for line in wide)


def test_clear_resets_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_cells(WIDTH)
    assert ml._frozen_buf is not None

    ml.clear()

    assert ml._frozen_buf is None
    assert ml._frozen_block_count == 0
    frozen_buf, live_lines = ml.render_split_cells(WIDTH)
    assert frozen_buf is None or frozen_buf.area.height == 0
    assert live_lines == []


def test_frozen_buf_cell_rows_render_identically_via_row_to_ansi() -> None:
    """Splicing frozen_buf's cells into a larger buffer must reproduce the
    exact same text as parsing the original ANSI lines directly — the core
    assumption TUI.render_cells relies on when it copies these rows by
    reference instead of re-parsing them."""
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 10)
    full_lines = _render_as_lines(ml, WIDTH)

    frozen_buf, _live_lines = ml.render_split_cells(WIDTH)
    assert frozen_buf is not None
    frozen_rows = frozen_buf.area.height

    target = Buffer.empty(Rect(0, 0, WIDTH + 2, 0))
    target.grow_to(frozen_rows)
    for r in range(frozen_rows):
        for x in range(WIDTH):
            cell = frozen_buf.get(x, r)
            target.set(x + 1, r, cell.symbol, cell.style)

    spliced_lines = [row_to_ansi(target, y)[1 : 1 + WIDTH].rstrip() for y in range(frozen_rows)]
    expected = [line.rstrip() for line in full_lines[:frozen_rows]]
    assert spliced_lines == expected


def test_large_finished_unit_freezes_once_something_follows_it() -> None:
    """A big finished terminal/tool output must eventually freeze (bounding
    per-frame cost), but never while it's still the last unit — a message
    the app considers "done" for the moment can still be mutated in place
    later (see test_last_unit_is_never_frozen_even_when_not_streaming), so
    "not streaming" alone isn't proof of finality. Once a further message
    exists after it, the app has moved on and it's safe to freeze."""
    ml = MessageList(theme=MessageTheme())
    ml.add_message(UserMessage.from_text("run the build"))
    huge_output = "\n".join(f"build log line {i}" for i in range(500))
    ml.add_message(AssistantMessage.from_text(huge_output))  # not streaming, but still last
    ml.add_message(UserMessage.from_text("looks good"))  # proves the previous unit is done

    _frozen_buf, live_lines = ml.render_split_cells(WIDTH)

    # Only the new trailing message stays live; the huge output got frozen.
    assert ml._frozen_block_count == len(ml._blocks) - 1
    assert len(live_lines) < 10


def test_finalized_large_unit_freezes_immediately_even_while_last() -> None:
    """Regression: a !shell-command's output (or the terminal tool's) can sit
    as the last message for a while if the user starts typing right after it
    finishes, before anything else is added — "not last" alone would leave it
    live (and slow) for that whole window. finalize() lets the driver
    (agent_hooks.py, at the exact point it drops its own reference to the
    block) prove immediately that nothing will touch it again."""
    ml = MessageList(theme=MessageTheme())
    ml.add_message(UserMessage.from_text("!ruff check"))
    huge_output = "\n".join(f"ruff output line {i}" for i in range(500))
    block = ml.add_message(AssistantMessage.from_text(huge_output))
    block.set_streaming(False)
    block.finalize()  # mirrors agent_hooks.py's terminal-execution-end handler

    _frozen_buf, live_lines = ml.render_split_cells(WIDTH)

    assert ml._frozen_block_count == len(ml._blocks)
    assert len(live_lines) == 0


def test_last_unit_is_never_frozen_even_when_not_streaming() -> None:
    """Regression: the interactive app creates an assistant's placeholder
    block at message_start with streaming=False (real streaming only starts
    once the first token lands), and can momentarily report streaming=False
    between token-batch flushes before the message is actually complete.
    Freezing is one-way and never re-checked, so freezing a not-yet-finished
    last unit permanently hides every token that streams in afterward —
    this reproduces exactly that: a message added non-streaming, then
    "streamed into" after the fact, must still show its final content."""
    ml = MessageList(theme=MessageTheme())
    ml.add_message(UserMessage.from_text("say hi"))
    # Mirrors message_start: placeholder added non-streaming, empty content.
    placeholder = ml.add_message(AssistantMessage.from_text(""), streaming=False)

    # A render happens here in the real app (request_render() after message_start).
    ml.render_split_cells(WIDTH)

    # Now the "stream" actually delivers content, exactly like _update_block.
    placeholder._message = AssistantMessage.from_text("Hi there!")
    placeholder.set_streaming(True)
    placeholder.invalidate()
    _frozen_buf, live_lines = ml.render_split_cells(WIDTH)
    assert any("Hi there!" in line for line in live_lines)

    # And once the turn ends (streaming=False for good, nothing further).
    placeholder.set_streaming(False)
    placeholder.invalidate()
    _frozen_buf, live_lines = ml.render_split_cells(WIDTH)
    assert any("Hi there!" in line for line in live_lines)
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_toggle_details_expanded_reaches_an_already_frozen_block() -> None:
    """Regression: a tool result can be marked "frozen" internally (something
    else was appended after it) while still fully visible on screen — frozen
    is not a reliable proxy for scrolled-off-screen. ctrl+o must still be
    able to expand/collapse it; this used to silently no-op instead."""
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 5)
    big_result = "\n".join(f"line {i}" for i in range(300))
    ml.add_message(
        ToolMessage(contents=[ToolResultContent(id="t1", tool_name="grep", content=big_result)])
    )
    ml.add_message(UserMessage.from_text("thanks"))  # pushes the tool result out of "last unit"
    ml.render_split_cells(WIDTH)
    assert ml._frozen_block_count == len(ml._blocks) - 1  # confirms it's actually frozen

    before = _render_as_lines(ml, WIDTH)
    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_toggle_details_expanded_still_affects_the_live_tail() -> None:
    """The still-live (unfrozen) trailing assistant message must respond to
    ctrl+o normally too."""
    ml = MessageList(theme=MessageTheme())
    ml.add_message(UserMessage.from_text("q"))
    long_thinking = "\n".join(f"thought {j}" for j in range(8))
    ml.add_message(
        AssistantMessage(
            contents=[ThinkingContent(content=long_thinking), TextContent(content="a")]
        )
    )
    before = _render_as_lines(ml, WIDTH)

    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_ctrl_o_still_works_after_a_reply_is_finalized_and_frozen() -> None:
    """A completed assistant reply should freeze immediately for input latency,
    while still remaining expandable/collapsible through explicit invalidation.
    """
    ml = MessageList(theme=MessageTheme())
    ml.add_message(UserMessage.from_text("explain"))
    long_thinking = "\n".join(f"reasoning {j}" for j in range(8))
    block = ml.add_message(
        AssistantMessage(contents=[ThinkingContent(content=long_thinking)]), streaming=True
    )
    ml.render_split_cells(WIDTH)  # mid-stream render, like a real session

    # Mirrors _on_message_end -> _update_block(msg, streaming=False, clear=True).
    block.set_streaming(False)
    block.invalidate()
    block.finalize()
    _frozen_buf, live_lines = ml.render_split_cells(WIDTH)
    assert ml._frozen_block_count == len(ml._blocks)
    assert live_lines == []

    before = _render_as_lines(ml, WIDTH)
    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)
