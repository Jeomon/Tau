"""Tests for interactive agent hook streaming behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tau.message.types import (
    AssistantMessage,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    Usage,
)
from tau.modes.interactive.agent_hooks import AgentHookHandler
from tau.modes.interactive.components.message_list import MessageBlock
from tau.tool.types import ToolResult


class _Spinner:
    def __init__(self) -> None:
        self.theme = SimpleNamespace(
            label_working="Working",
            label_thinking="Thinking",
            label_streaming="Streaming",
            label_tool_calling="Running",
            label_compacting="Compacting",
        )
        self.label = ""
        self.token_updates: list[tuple[int | None, int]] = []

    def set_label(self, label: str) -> None:
        self.label = label

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def start_turn(self, input_estimate: int = 0) -> None:
        pass

    def update_tokens(self, *, up: int | None = None, down: int = 0) -> None:
        self.token_updates.append((up, down))

    def set_streaming_estimate(self, tokens: int) -> None:
        pass

    def push_reason(self, key: str, label: str) -> None:
        pass

    def pop_reason(self, key: str) -> None:
        pass


class _Layout:
    def __init__(self) -> None:
        self.spinner = _Spinner()
        self.blocks: list[MessageBlock] = []

    def add_message(self, message: object, streaming: bool = False) -> MessageBlock:
        block = MessageBlock(message, streaming=streaming)
        self.blocks.append(block)
        return block


class _TUI:
    def __init__(self) -> None:
        self.render_requests = 0

    def request_render(self) -> None:
        self.render_requests += 1


@pytest.mark.anyio
async def test_partial_tool_result_updates_existing_block_until_final_result() -> None:
    layout = _Layout()
    tui = _TUI()
    handler = AgentHookHandler(
        SimpleNamespace(),  # type: ignore[arg-type]
        layout,  # type: ignore[arg-type]
        tui,  # type: ignore[arg-type]
    )
    tool_call = ToolCallContent(id="call-1", name="terminal", args={"cmd": "build"})
    await handler._on_tool_start(SimpleNamespace(tool_call=tool_call))

    await handler._on_tool_update(
        SimpleNamespace(
            partial_tool_result=ToolResult.ok(
                "call-1",
                "first\nsecond",
                metadata={"running": True},
            )
        )
    )
    await handler._on_tool_update(
        SimpleNamespace(
            partial_tool_result=ToolResult.ok(
                "call-1",
                "first\nsecond\nthird",
                metadata={"running": True},
            )
        )
    )

    assert len(layout.blocks) == 1
    block = layout.blocks[0]
    assert block.is_streaming
    assert isinstance(block._message, ToolMessage)
    assert block._message.contents[0].content.endswith("third")
    assert block._message.contents[0].tool_name == "terminal"

    final_message = ToolMessage.from_result(
        ToolResultContent(
            id="call-1",
            content="first\nsecond\nthird\ndone",
            tool_name="terminal",
        )
    )
    await handler._on_message_start(SimpleNamespace(message=final_message))
    await handler._on_message_end(SimpleNamespace(message=final_message))

    assert len(layout.blocks) == 1
    assert not block.is_streaming
    assert block._message is final_message


def _handler() -> tuple[AgentHookHandler, _Layout]:
    layout = _Layout()
    handler = AgentHookHandler(
        SimpleNamespace(),  # type: ignore[arg-type]
        layout,  # type: ignore[arg-type]
        _TUI(),  # type: ignore[arg-type]
    )
    return handler, layout


@pytest.mark.anyio
async def test_up_count_includes_cache_tokens() -> None:
    # Anthropic-style usage: on a warm cache input_tokens is tiny and the
    # prompt bulk sits in cache_read/cache_write — the spinner's ↑ must show
    # their sum, not collapse to the uncached slice.
    handler, layout = _handler()
    msg = AssistantMessage.from_text("hi")
    msg.usage = Usage(
        input_tokens=3,
        output_tokens=50,
        cache_read_tokens=40_000,
        cache_write_tokens=1_000,
        input_tokens_include_cache_read=False,
    )
    await handler._on_message_end(SimpleNamespace(message=msg))
    assert layout.spinner.token_updates == [(41_003, 50)]


@pytest.mark.anyio
async def test_up_count_no_double_count_when_input_includes_cache() -> None:
    # OpenAI-style usage: input_tokens already folds in cached tokens.
    handler, layout = _handler()
    msg = AssistantMessage.from_text("hi")
    msg.usage = Usage(
        input_tokens=41_000,
        output_tokens=50,
        cache_read_tokens=40_000,
        input_tokens_include_cache_read=True,
    )
    await handler._on_message_end(SimpleNamespace(message=msg))
    assert layout.spinner.token_updates == [(41_000, 50)]


@pytest.mark.anyio
async def test_empty_usage_does_not_reset_up_count() -> None:
    # A provider that reports no usage must not clobber the ↑ count with 0;
    # the finished message's tokenizer estimate feeds the ↓ count instead.
    handler, layout = _handler()
    msg = AssistantMessage.from_text("some response text")
    msg.usage = Usage()
    await handler._on_message_end(SimpleNamespace(message=msg))
    assert len(layout.spinner.token_updates) == 1
    up, down = layout.spinner.token_updates[0]
    assert up is None
    assert down > 0
