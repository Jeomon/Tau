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


@pytest.mark.anyio
async def test_typewriter_reveal_paces_a_large_burst() -> None:
    """A single message_update carrying a big jump in text (a burst of
    buffered tokens landing at once) must not pop onto the screen in one
    flush — it should reveal gradually across several flush ticks instead."""
    handler, layout = _handler()
    start_msg = AssistantMessage.from_text("")
    await handler._on_message_start(SimpleNamespace(message=start_msg))

    burst = "x" * 5_000
    await handler._on_message_update(SimpleNamespace(message=AssistantMessage.from_text(burst)))

    handler._flush_pending()
    block = layout.blocks[0]
    first_len = len(block.message.text_content())
    assert 0 < first_len < len(burst), (
        "first flush after a big burst should reveal a bounded prefix, not everything at once"
    )

    # Keep draining the backlog (mirrors the self-rescheduled flush ticks)
    # until the whole burst has been revealed.
    for _ in range(10_000):
        if handler._pending_flush_handle is None:
            break
        handler._pending_flush_handle.cancel()
        handler._flush_pending()
    assert block.message.text_content() == burst


@pytest.mark.anyio
async def test_message_end_always_shows_full_text_immediately() -> None:
    """Completion must never be delayed by the typewriter catch-up animation —
    message_end always displays the true final text right away."""
    handler, layout = _handler()
    start_msg = AssistantMessage.from_text("")
    await handler._on_message_start(SimpleNamespace(message=start_msg))

    burst = "y" * 5_000
    mid_msg = AssistantMessage.from_text(burst)
    await handler._on_message_update(SimpleNamespace(message=mid_msg))
    handler._flush_pending()
    block = layout.blocks[0]
    assert len(block.message.text_content()) < len(burst)  # still catching up

    final_msg = AssistantMessage.from_text(burst + " done.")
    await handler._on_message_end(SimpleNamespace(message=final_msg))
    assert block.message.text_content() == burst + " done."
    assert not block.is_streaming


@pytest.mark.anyio
async def test_typewriter_reveal_does_not_lag_normal_speed_streaming() -> None:
    """Ordinary small, evenly-paced deltas (well under the typewriter cap)
    must still show up promptly — the cap should only ever bite on bursts."""
    handler, layout = _handler()
    start_msg = AssistantMessage.from_text("")
    await handler._on_message_start(SimpleNamespace(message=start_msg))

    text = ""
    for word in ["Hello", " there", ", how", " are", " you", " today?"]:
        text += word
        await handler._on_message_update(SimpleNamespace(message=AssistantMessage.from_text(text)))
        handler._flush_pending()

    block = layout.blocks[0]
    assert block.message.text_content() == text
