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


def _display_name(tool_name: str) -> str:
    """Convert a snake_case tool name to Title Case, matching the TUI's naming."""
    return " ".join(w.capitalize() for w in tool_name.split("_"))


def _call_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Pick the 1-2 most meaningful args for a one-line call summary.

    Mirrors each builtin tool's TUI `render_call` (see tau/builtins/tools/*.py
    and tau/builtins/extensions/web/tools/*.py) so a call reads e.g.
    "Web Fetch(https://wired.com/...)" instead of a raw `{"url": "..."}` dump.
    """
    if tool_name == "web_fetch":
        values: list[Any] = [args.get("prompt") or args.get("url", "")]
    elif tool_name == "web_search":
        values = [args.get("query", "")]
    elif tool_name == "grep":
        values = [" ".join(str(args.get("pattern", "")).split()), args.get("path", "")]
    elif tool_name in {"edit", "write", "read", "ls"}:
        values = [args.get("path", "")]
    elif tool_name == "glob":
        values = [args.get("pattern", "")]
    elif tool_name == "terminal":
        values = [args.get("cmd", "")]
    else:
        values = [v for v in args.values() if isinstance(v, (str, int, float)) and str(v)]
    joined = ", ".join(str(v).replace("\n", " ") for v in values if v)
    return joined[:79] + "…" if len(joined) > 80 else joined


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

    name = _display_name(block.name)
    summary = _call_summary(block.name, block.args)
    header = f"{name}({summary})" if summary else name
    with (
        ui.expansion(header)
        .classes(f"w-full tau-tool-block {status_classes}")
        .props('dense expand-icon="expand_more"')
    ):
        if result is not None and result.content:
            result_color = "text-[#f87171]" if is_error else "text-[var(--text-muted)]"
            ui.markdown(result.content).classes(f"text-xs whitespace-pre-wrap {result_color}")
        with ui.expansion("Arguments").classes("tau-tool-args-block").props('dense expand-icon="expand_more"'):
            ui.markdown(f"```json\n{json.dumps(block.args, indent=2)}\n```").classes("text-xs")
