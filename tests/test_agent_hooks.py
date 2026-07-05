"""Tests for interactive agent hook streaming behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tau.message.types import ToolCallContent, ToolMessage, ToolResultContent
from tau.modes.interactive.agent_hooks import AgentHookHandler
from tau.modes.interactive.components.message_list import MessageBlock
from tau.tool.types import ToolResult


class _Spinner:
    def __init__(self) -> None:
        self.theme = SimpleNamespace(label_thinking="Thinking", label_tool_calling="Running")
        self.label = ""

    def set_label(self, label: str) -> None:
        self.label = label

    def start(self) -> None:
        pass

    def stop(self) -> None:
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
