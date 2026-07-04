"""render_split_cells must always agree byte-for-byte with the full render(width)

MessageList caches "finalized" render units as Cell rows (render_split_cells)
so a long session doesn't re-parse and re-diff its entire history every
frame. These tests pin the one invariant that matters: whatever the cache
returns, converted back to text, must be identical to what the slow,
always-correct full-render path (_render_blocks/render) produces — across
growth, streaming, undo, toggling, theme changes, and resize.
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
from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.theme import MessageTheme

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
    lines.extend(line.rstrip() for line in live_lines)
    return lines


def _add_conversation(ml: MessageList, n: int) -> None:
    for i in range(n):
        ml.add_message(UserMessage.from_text(f"question number {i}"))
        ml.add_message(AssistantMessage.from_text(f"answer number {i} " * 3))


def test_split_matches_full_render_as_history_grows() -> None:
    ml = MessageList(theme=MessageTheme())
    for i in range(40):
        ml.add_message(UserMessage.from_text(f"question {i}"))
        ml.add_message(AssistantMessage.from_text(f"answer {i}"))
        assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


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

    assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


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
        assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]

    streaming_block.set_streaming(False)
    assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


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
    assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


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
    before = ml.render(WIDTH)
    ml.render_split_cells(WIDTH)  # populate the frozen cache

    ml.toggle_details_expanded()

    assert ml.render(WIDTH) != before  # sanity: toggling actually changed output
    assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


def test_set_theme_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_cells(WIDTH)

    from tau.tui.style import Style

    new_theme = MessageTheme(you_label=Style())
    ml.set_theme(new_theme)

    assert _split_as_lines(ml, WIDTH) == [line.rstrip() for line in ml.render(WIDTH)]


def test_width_change_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_cells(WIDTH)

    narrower = WIDTH - 10
    assert _split_as_lines(ml, narrower) == [line.rstrip() for line in ml.render(narrower)]


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
    full_lines = ml.render(WIDTH)

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
