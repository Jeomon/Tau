"""MessageList's string rendering: incremental correctness and wrapping.

The cell-vs-string comparisons that used to live here went with the cell path.
The invariants they protected — that the split cache agrees with rendering
everything, across growth, expansion and width changes — are now in
test_message_list_frozen.py, asserted against the string cache directly.

What remains here is what that file does not cover: that building a list
incrementally matches building it in one go, that the frozen prefix is
reported correctly for stable_through, and that wrap_to_rows agrees with the
exact wrapper on content its ASCII fast path cannot take.
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
from tau.modes.interactive.components.message_list import (
    MessageBlock,
    MessageList,
    _wrap_to_rows,
)
from tau.tui.ansi_text import tokenize, wrap_ansi

WIDTH = 80

MARKDOWN = (
    "Here is a paragraph with `inline code`, **bold** and _emphasis_ that is "
    "long enough to wrap across a couple of terminal lines.\n\n"
    "```python\ndef example(a, b):\n    return a + b\n```\n\n"
    "- bullet one\n- bullet two\n"
)


def _session(turns: int = 4, output_lines: int = 30, unicode: bool = False) -> MessageList:
    ml = MessageList()
    for i in range(turns):
        u = MessageBlock(UserMessage(contents=[TextContent(content=f"task {i}")]))
        u.finalize()
        ml.add_block(u)

        text = MARKDOWN if not unicode else MARKDOWN + "\n日本語 🎉 👨\u200d👩\u200d👧 🇯🇵 café\n"
        a = MessageBlock(
            AssistantMessage(
                contents=[
                    TextContent(content=text),
                    ToolCallContent(id=f"c{i}", name="bash", args={"command": "ls"}),
                ]
            )
        )
        a.finalize()
        ml.add_block(a)

        body = "\n".join(f"output line {j} for call {i}" for j in range(output_lines))
        t = MessageBlock(
            ToolMessage(contents=[ToolResultContent(id=f"c{i}", tool_name="bash", content=body)])
        )
        t.finalize()
        ml.add_block(t)
    return ml


def _same_pixels(a: list[str], b: list[str], width: int = WIDTH) -> bool:  # noqa: ARG001
    """Compare rendered result, not bytes: the paths emit equivalent SGR runs."""
    if len(a) != len(b):
        return False
    return all(
        [(cluster, style) for cluster, _w, style in tokenize(ra)]
        == [(cluster, style) for cluster, _w, style in tokenize(rb)]
        for ra, rb in zip(a, b, strict=True)
    )


def test_incremental_append_matches_a_fresh_build() -> None:
    """The frozen cache must extend, not diverge, as blocks arrive."""
    incremental = MessageList()
    for i in range(6):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"message {i}")]))
        blk.finalize()
        incremental.add_block(blk)
        incremental.render(WIDTH)

    fresh = MessageList()
    for i in range(6):
        blk = MessageBlock(UserMessage(contents=[TextContent(content=f"message {i}")]))
        blk.finalize()
        fresh.add_block(blk)

    assert incremental.render(WIDTH) == fresh.render(WIDTH)


def test_frozen_prefix_is_reported_for_stable_through() -> None:
    ml = _session(turns=3)
    frozen, live = ml.render_split_lines(WIDTH)
    assert ml.frozen_row_count == len(frozen)
    assert ml.render(WIDTH)[: len(frozen)] == frozen
    assert len(live) >= 0


def test_frozen_rows_are_stable_across_renders() -> None:
    ml = _session(turns=3)
    first, _ = ml.render_split_lines(WIDTH)
    snapshot = list(first)
    ml.render_split_lines(WIDTH)
    again, _ = ml.render_split_lines(WIDTH)
    assert again[: len(snapshot)] == snapshot


class TestWrapToRows:
    def test_plain_ascii_that_fits_is_passed_through(self) -> None:
        assert _wrap_to_rows("hello world", 40) == ["hello world"]

    def test_long_ascii_wraps(self) -> None:
        rows = _wrap_to_rows("word " * 30, 40)
        assert len(rows) > 1

    @pytest.mark.parametrize(
        "line",
        [
            "日本語のテキストです",
            "🎉 party 👨\u200d👩\u200d👧 family",
            "🇯🇵 flag",
            "e\u0301 combining",
            "\x1b[31mstyled\x1b[0m",
            "",
        ],
    )
    def test_non_ascii_takes_the_exact_wrapper(self, line: str) -> None:
        """Content the ASCII fast path must refuse falls through to wrap_ansi."""
        assert _wrap_to_rows(line, 20) == wrap_ansi(line, 20)
