"""Rows that just became frozen must still be repainted.

Reported symptom: while a tool call runs, the spinner and streamed text are
live rows. When the call settles those rows move into MessageList's frozen
prefix. If the renderer is told that prefix is "stable", it skips re-diffing
them — so the *old* live content (a spinner frame, a half-streamed line) is
never overwritten and stays on screen, mixed into the tool output. Resizing
appears to fix it, because that forces a full repaint.

A row is only safe to skip if it was ALSO frozen last frame, and only if the
frozen cache was not rebuilt.
"""

from __future__ import annotations

from tau.message.types import AssistantMessage, TextContent, UserMessage
from tau.modes.interactive.components.message_list import MessageBlock, MessageList

WIDTH = 40


def _tui(children):
    from tau.tui.service import TUI

    t = TUI.__new__(TUI)
    t.children = list(children)
    t._child_rows = {}
    t._stable_rows = 0
    t._prev_stable_rows = 0
    t._child_frozen_gen = {}
    t.cursor_position = None
    return t


def _block(text: str, *, settled: bool) -> MessageBlock:
    b = MessageBlock(AssistantMessage(contents=[TextContent(content=text)]))
    if settled:
        b.finalize()
    return b


def test_newly_frozen_rows_are_not_declared_stable() -> None:
    """The reported bug: live rows settle, and must be repainted, not skipped."""
    ml = MessageList()
    first = MessageBlock(UserMessage(contents=[TextContent(content="go")]))
    first.finalize()
    ml.add_block(first)

    live = _block("streaming output still arriving", settled=False)
    ml.add_block(live)

    tui = _tui([ml])
    tui.render(WIDTH)
    stable_while_live = tui._stable_rows

    # The call settles: those rows move from live into the frozen prefix.
    live.finalize()
    ml.add_block(_block("next", settled=False))
    tui.render(WIDTH)

    frozen, _ = ml.render_split_lines(WIDTH)
    # The newly-frozen rows must NOT be inside the stable span, or the diff
    # skips them and the old live rendering stays on screen.
    assert tui._stable_rows <= stable_while_live or tui._stable_rows < len(frozen)
    assert tui._stable_rows < len(frozen)


def test_stable_span_grows_only_after_a_row_has_been_painted_once() -> None:
    ml = MessageList()
    for i in range(3):
        b = MessageBlock(UserMessage(contents=[TextContent(content=f"m{i}")]))
        b.finalize()
        ml.add_block(b)
    ml.add_block(_block("live", settled=False))

    tui = _tui([ml])
    tui.render(WIDTH)
    first = tui._stable_rows
    tui.render(WIDTH)
    second = tui._stable_rows

    assert first == 0  # nothing was frozen last frame yet
    assert second > 0  # now the prefix has survived a frame


def test_rebuilt_frozen_cache_forces_a_full_rediff() -> None:
    """ctrl+O / theme swap can keep the row count while every row differs."""
    ml = MessageList()
    for i in range(4):
        # Assistant blocks, because toggle_details_expanded only touches
        # assistant/tool messages -- with user messages it returns early and
        # never bumps the generation.
        ml.add_block(_block(f"reply {i}", settled=True))

    tui = _tui([ml])
    tui.render(WIDTH)
    tui.render(WIDTH)
    assert tui._stable_rows > 0

    ml.toggle_details_expanded()  # bumps frozen_generation
    tui.render(WIDTH)
    assert tui._stable_rows == 0


def test_stable_span_never_exceeds_the_frozen_prefix() -> None:
    ml = MessageList()
    for i in range(5):
        b = MessageBlock(UserMessage(contents=[TextContent(content=f"m{i}")]))
        b.finalize()
        ml.add_block(b)
    ml.add_block(_block("live tail", settled=False))

    tui = _tui([ml])
    for _ in range(4):
        tui.render(WIDTH)
        frozen, _live = ml.render_split_lines(WIDTH)
        assert tui._stable_rows <= len(frozen)


def test_children_above_message_list_are_counted_in_the_offset() -> None:
    """stable_rows is absolute, so header/spacer rows shift it."""
    from tau.tui.component import StaticComponent

    ml = MessageList()
    for i in range(3):
        b = MessageBlock(UserMessage(contents=[TextContent(content=f"m{i}")]))
        b.finalize()
        ml.add_block(b)
    ml.add_block(_block("live", settled=False))

    header = StaticComponent(["== header =="])
    tui = _tui([header, ml])
    tui.render(WIDTH)
    tui.render(WIDTH)

    frozen, _ = ml.render_split_lines(WIDTH)
    header_rows = len(header.render(WIDTH))
    assert tui._stable_rows <= header_rows + len(frozen)
    assert tui._stable_rows >= header_rows
