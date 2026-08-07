"""A streamed reply must not get *shorter* as it arrives.

The renderer paints one frame containing the whole transcript, with the input
box at the end. Anything that reduces the frame's height mid-stream shifts
every row below it — the divider, the input, the footer — up by that much, and
the next token shifts them back down. Read on screen, that is the input box
twitching.

The cause was a partial closing fence. `_hold_open_inline` skipped the hold
entirely inside a fenced code block, on the grounds that every delimiter in
there is literal. That is true of *inline* delimiters and false of the fence's
own: a line of one or two backticks is either code content or the closing
delimiter still arriving. It rendered as content and then vanished when the
third backtick landed — one row, on every code block in every streamed reply.
"""

from __future__ import annotations

import pytest

from tau.message.types import AssistantMessage, TextContent
from tau.modes.interactive.components.message_list import MessageList
from tau.tui.markdown import render_markdown
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi


def _heights(text: str, width: int = 60) -> list[int]:
    """Frame height after each streamed character, as the UI would see it."""
    messages = MessageList(height=20, theme=MessageTheme())
    message = AssistantMessage(contents=[TextContent(content="")])
    block = messages.add_message(message, streaming=True)

    out: list[int] = []
    for i in range(1, len(text) + 1):
        message.contents[0].content = text[:i]
        block.invalidate()
        out.append(len(messages.render(width)))
    return out


def _shrinks(text: str, width: int = 60) -> list[tuple[int, int, int]]:
    heights = _heights(text, width)
    return [
        (i, heights[i - 1], heights[i])
        for i in range(1, len(heights))
        if heights[i] < heights[i - 1]
    ]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("backtick fence", "before\n\n```python\nx = 1\ny = 2\n```\nafter."),
        ("tilde fence", "before\n\n~~~\nx = 1\n~~~\nafter."),
        ("two fences", "a\n\n```py\n1\n```\n\nb\n\n```py\n2\n```\nend."),
        ("inline then fence", "Use **bold** and `code`:\n\n```sh\nls -la\n```\ndone."),
        ("fence with no language", "x\n\n```\nplain\n```\ny"),
        ("indented closing fence", "x\n\n```\nplain\n  ```\ny"),
    ],
)
def test_streaming_never_shrinks(name: str, text: str) -> None:
    shrinks = _shrinks(text)

    assert not shrinks, f"{name}: frame shrank at {shrinks} — everything below jumps up"


def _streamed(text: str, width: int = 40) -> list[str]:
    """What the UI shows mid-stream — the path the hold-back applies to.

    ``render_markdown`` is the one-shot renderer and deliberately does *not*
    hold anything back: a finished document has no ambiguity left to wait on.
    """
    messages = MessageList(height=20, theme=MessageTheme())
    message = AssistantMessage(contents=[TextContent(content=text)])
    messages.add_message(message, streaming=True)
    return [strip_ansi(line).strip() for line in messages.render(width)]


def test_the_partial_fence_is_not_shown_as_code_content() -> None:
    """The transient was visible as well as jumpy: '``' rendered inside the block."""
    assert "``" not in _streamed("```\nx = 1\n``")


def test_a_fence_that_really_contains_backticks_still_renders_them() -> None:
    """Holding back must delay the line, never drop it."""
    rendered = [
        strip_ansi(line).strip()
        for line in render_markdown("```\na\n``\nb\n```\ndone", 40, MessageTheme().markdown)
    ]

    assert "``" in rendered, "a legitimate '``' code line was swallowed"
    assert "a" in rendered and "b" in rendered


def test_the_held_line_appears_once_it_resolves() -> None:
    """One frame of latency, not permanent suppression."""
    assert "``" not in _streamed("```\nx\n``")
    assert "``!" in _streamed("```\nx\n``!\n")


def test_plain_prose_is_unaffected() -> None:
    """The hold only applies inside a fence; ordinary text must not be delayed."""
    text = "A sentence that wraps across more than one line of a narrow terminal, plainly."

    assert not _shrinks(text)
    assert "plainly." in strip_ansi("".join(render_markdown(text, 40, MessageTheme().markdown)))
