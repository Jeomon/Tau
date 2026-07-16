from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

if TYPE_CHECKING:
    from tau.message.types import ThinkingContent, ToolCallContent, ToolResultContent

MessageRole = Literal["assistant", "user"]


def _format_time(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


@dataclass
class RenderedMessage:
    """NiceGUI elements that make up one rendered chat message."""

    root: Any
    content: Any

    def delete(self) -> None:
        """Remove this message from the page."""
        self.root.delete()

    def update_content(self, text: str) -> None:
        """Update the rendered message body."""
        self.content.content = text
        self.content.update()


class MessageView:
    """Renderer for one browser chat message."""

    def __init__(
        self,
        text: str,
        *,
        role: MessageRole,
        timestamp: float | None = None,
        streaming: bool = False,
    ) -> None:
        self._text = text
        self._role = role
        self._timestamp = timestamp
        self._streaming = streaming

    def render(self) -> RenderedMessage:
        """Render the message into the current NiceGUI container."""
        streaming_class = " tau-msg-streaming" if self._streaming else ""
        root = ui.column().classes(
            f"w-full gap-0 tau-msg-row{streaming_class} {self._alignment_class()}"
        )
        with root:
            with ui.element("div").classes(self._bubble_classes()):
                content = ui.markdown(self._text).classes("max-w-none text-sm text-[var(--text)]")
            if not self._streaming:
                with ui.row().classes("items-center gap-1 px-1 tau-msg-meta"):
                    if self._role == "assistant":
                        ui.button(
                            icon="content_copy",
                            on_click=lambda: ui.clipboard.write(content.content),
                        ).props("flat dense round size=sm").classes("tau-msg-copy-btn")
                    time_label = _format_time(self._timestamp)
                    if time_label:
                        ui.label(time_label).classes("text-[11px] text-[var(--text-dim)]")
        return RenderedMessage(root=root, content=content)

    def _alignment_class(self) -> str:
        return "items-end" if self._role == "user" else "items-start"

    def _bubble_classes(self) -> str:
        if self._role == "user":
            return "max-w-[85%] px-3 py-2 tau-bubble-user"
        return "w-full px-0 py-0 tau-bubble-assistant"


def _tool_preview(args: dict[str, Any]) -> str:
    """Short single-line preview of a tool call's arguments."""
    try:
        preview = json.dumps(args, separators=(", ", ": "))
    except (TypeError, ValueError):
        preview = str(args)
    return preview[:80] + ("…" if len(preview) > 80 else "")


def render_thinking_block(block: ThinkingContent) -> None:
    """Render a collapsed 'Thinking' panel, matching pi-web's ThinkingBlock."""
    with (
        ui.expansion("Thinking")
        .classes("w-full tau-thinking-block")
        .props('dense expand-icon="expand_more"')
    ):
        ui.markdown(block.content).classes("text-xs text-[var(--text-muted)] whitespace-pre-wrap")


def render_tool_call_block(block: ToolCallContent, result: ToolResultContent | None) -> None:
    """Render a collapsed tool-call panel with args and paired result."""
    is_error = bool(result and result.is_error)
    status_classes = "tau-tool-error" if is_error else "tau-tool-ok"

    header = f"{block.name}  {_tool_preview(block.args)}"
    with (
        ui.expansion(header)
        .classes(f"w-full tau-tool-block {status_classes}")
        .props('dense expand-icon="expand_more"')
    ):
        ui.markdown(f"```json\n{json.dumps(block.args, indent=2)}\n```").classes("text-xs")
        if result is not None and result.content:
            result_color = "text-[#f87171]" if is_error else "text-[var(--text-muted)]"
            ui.markdown(result.content).classes(f"text-xs whitespace-pre-wrap {result_color}")
