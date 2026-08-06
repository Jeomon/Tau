"""render_split_lines must agree with rendering the whole list.

MessageList caches "finalized" render units as ANSI rows
(``render_split_lines``) so a long session doesn't re-render its entire
history every frame. These tests pin the invariant that matters: whatever the
cache returns must produce the same content as rendering everything — across
growth, streaming, undo, toggling, theme changes and resize — plus the
freezing rules themselves (the last unit is never frozen, a finalized unit
freezes immediately, and so on).

Ported from the Cell-based cache these originally covered; the invariants are
the cache's, not the representation's.
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
from tau.tui.compose import wrap_to_rows
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi, visible_width

WIDTH = 60


def _split_as_lines(ml: MessageList, width: int) -> list[str]:
    """The cache's output: frozen rows plus the still-live tail, wrapped."""
    frozen, live = ml.render_split_lines(width)
    lines = [x.rstrip() for x in frozen]
    for line in live:
        lines.extend(x.rstrip() for x in wrap_to_rows(line, width))
    return lines


def _render_as_lines(ml: MessageList, width: int) -> list[str]:
    """Render the complete message list through its component contract."""
    return [x.rstrip() for x in ml.render(width)]


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
    ml.render_split_lines(WIDTH)
    frozen_before = list(ml._frozen_lines)
    frozen_rows_before = len(frozen_before)
    assert frozen_rows_before > 0

    ml.add_message(UserMessage.from_text("new question"))
    ml.add_message(AssistantMessage.from_text("new answer"))
    ml.render_split_lines(WIDTH)

    # The frozen prefix only ever grows; rows already frozen stay byte-identical.
    assert len(ml._frozen_lines) >= frozen_rows_before
    for y in range(frozen_rows_before):
        assert ml._frozen_lines[y] == frozen_before[y]


def test_undo_pops_only_the_live_tail() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 20)
    ml.render_split_lines(WIDTH)
    frozen_before = ml._lines_block_count
    assert frozen_before > 0

    ml.add_message(UserMessage.from_text("oops"))
    assert ml.remove_last()

    assert ml._lines_block_count == frozen_before
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
    ml.render_split_lines(WIDTH)  # populate the frozen cache

    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before  # sanity: toggling actually changed output
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_set_theme_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_lines(WIDTH)

    from tau.tui.style import Style

    new_theme = MessageTheme(you_label=Style())
    ml.set_theme(new_theme)

    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)


def test_width_change_invalidates_frozen_cache() -> None:
    ml = MessageList(theme=MessageTheme())
    _add_conversation(ml, 15)
    ml.render_split_lines(WIDTH)

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
    ml.render_split_lines(WIDTH)
    assert ml._frozen_lines

    ml.clear()

    assert ml._frozen_lines == []
    assert ml._lines_block_count == 0
    frozen_buf, live_lines = ml.render_split_lines(WIDTH)
    assert frozen_buf == []
    assert live_lines == []


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

    _frozen_lines, live_lines = ml.render_split_lines(WIDTH)

    # Only the new trailing message stays live; the huge output got frozen.
    assert ml._lines_block_count == len(ml._blocks) - 1
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

    _frozen_lines, live_lines = ml.render_split_lines(WIDTH)

    assert ml._lines_block_count == len(ml._blocks)
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
    ml.render_split_lines(WIDTH)

    # Now the "stream" actually delivers content, exactly like _update_block.
    placeholder._message = AssistantMessage.from_text("Hi there!")
    placeholder.set_streaming(True)
    placeholder.invalidate()
    _frozen_lines, live_lines = ml.render_split_lines(WIDTH)
    assert any("Hi there!" in line for line in live_lines)

    # And once the turn ends (streaming=False for good, nothing further).
    placeholder.set_streaming(False)
    placeholder.invalidate()
    _frozen_lines, live_lines = ml.render_split_lines(WIDTH)
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
    ml.render_split_lines(WIDTH)
    assert ml._lines_block_count == len(ml._blocks) - 1  # confirms it's actually frozen

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
    ml.render_split_lines(WIDTH)  # mid-stream render, like a real session

    # Mirrors _on_message_end -> _update_block(msg, streaming=False, clear=True).
    block.set_streaming(False)
    block.invalidate()
    block.finalize()
    _frozen_lines, live_lines = ml.render_split_lines(WIDTH)
    assert ml._lines_block_count == len(ml._blocks)
    assert live_lines == []

    before = _render_as_lines(ml, WIDTH)
    ml.toggle_details_expanded()

    assert _render_as_lines(ml, WIDTH) != before
    assert _split_as_lines(ml, WIDTH) == _render_as_lines(ml, WIDTH)
