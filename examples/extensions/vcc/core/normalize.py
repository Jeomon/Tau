from __future__ import annotations

from typing import Any

from .blocks import Block
from .sanitize import sanitize


def _import_message_types() -> dict[str, Any]:
    """Lazily import tau message classes (avoids import cost at module load)."""
    from tau.message.types import (
        AssistantMessage,
        ImageContent,
        TerminalExecutionMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )

    return {
        "AssistantMessage": AssistantMessage,
        "ImageContent": ImageContent,
        "TerminalExecutionMessage": TerminalExecutionMessage,
        "TextContent": TextContent,
        "ThinkingContent": ThinkingContent,
        "ToolCallContent": ToolCallContent,
        "ToolMessage": ToolMessage,
        "ToolResultContent": ToolResultContent,
        "UserMessage": UserMessage,
    }


def _normalize_one(msg: Any, msg_index: int, T: dict[str, Any]) -> list[Block]:
    if isinstance(msg, T["UserMessage"]):
        blocks: list[Block] = []
        text = sanitize(
            "\n".join(c.content for c in msg.contents if isinstance(c, T["TextContent"]))
        )
        if text:
            blocks.append(Block(kind="user", text=text, source_index=msg_index))
        for c in msg.contents:
            if isinstance(c, T["ImageContent"]):
                note = c.dimension_note or "image"
                blocks.append(Block(kind="user", text=f"[image: {note}]", source_index=msg_index))
        return blocks or [Block(kind="user", text="", source_index=msg_index)]

    if isinstance(msg, T["TerminalExecutionMessage"]):
        return [
            Block(
                kind="bash",
                command=msg.command or "",
                output=msg.output or "",
                exit_code=msg.exit_code,
                source_index=msg_index,
            )
        ]

    if isinstance(msg, T["ToolMessage"]):
        blocks = []
        for c in msg.contents:
            if isinstance(c, T["ToolResultContent"]):
                blocks.append(
                    Block(
                        kind="tool_result",
                        name=c.tool_name or "",
                        text=sanitize(c.content or ""),
                        source_index=msg_index,
                    )
                )
        return blocks

    if isinstance(msg, T["AssistantMessage"]):
        blocks = []
        for c in msg.contents:
            if isinstance(c, T["TextContent"]):
                blocks.append(
                    Block(kind="assistant", text=sanitize(c.content), source_index=msg_index)
                )
            elif isinstance(c, T["ToolCallContent"]):
                blocks.append(
                    Block(
                        kind="tool_call",
                        name=c.name,
                        args=dict(c.args or {}),
                        source_index=msg_index,
                    )
                )
            # ThinkingContent is intentionally dropped from the brief.
        return blocks

    return []


def normalize(messages: list[Any]) -> list[Block]:
    """Convert tau ``AgentMessage`` objects to normalized :class:`Block` list."""
    T = _import_message_types()
    out: list[Block] = []
    for i, msg in enumerate(messages):
        out.extend(_normalize_one(msg, i, T))
    return out
