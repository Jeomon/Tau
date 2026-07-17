from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

from tau.tui.markdown import render_latex_math

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
        self.content.content = render_latex_math(text)
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
                content = ui.markdown(render_latex_math(self._text)).classes(
                    "max-w-none text-sm text-[var(--text)]"
                )
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


def _format_tool_name(tool_name: str) -> str:
    """"web_search" -> "Web Search", "ls" -> "Ls" — split on underscore, title-case each word."""
    return " ".join(word.capitalize() for word in tool_name.split("_"))


def render_thinking_block(block: ThinkingContent) -> None:
    """Render a collapsed 'Thinking' panel, matching pi-web's ThinkingBlock."""
    with (
        ui.expansion("Thinking")
        .classes("w-full tau-thinking-block")
        .props('dense expand-icon="expand_more"')
    ):
        ui.markdown(render_latex_math(block.content)).classes(
            "text-xs text-[var(--text-muted)] whitespace-pre-wrap"
        )


def render_tool_call_block(block: ToolCallContent, result: ToolResultContent | None) -> None:
    """Render a tool-call block matching pi-web's ToolCallBlock: a bold
    colored verb (green/red) + gray monospace arg preview in the header,
    collapsed by default, with raw args and the result only shown once
    expanded. ui.expansion() only supports a plain-string header, so this
    is a hand-rolled toggle to get independently-colored header spans.
    """
    is_error = bool(result and result.is_error)
    status_classes = "tau-tool-error" if is_error else "tau-tool-ok"
    preview = _call_summary(block.name, block.args)
    name_color = "#f87171" if is_error else "#16a34a"

    expanded = [False]
    details_container: dict[str, Any] = {}
    chevron_ref: dict[str, Any] = {}

    def toggle() -> None:
        expanded[0] = not expanded[0]
        details_container["el"].set_visibility(expanded[0])
        chevron_ref["el"].classes(toggle="tau-tool-chevron-open")

    with ui.column().classes(f"w-full gap-0 tau-tool-block {status_classes}"):
        with ui.row().classes(
            "w-full items-center gap-2 px-2.5 py-1.5 cursor-pointer tau-tool-header"
        ).on("click", toggle):
            ui.label(_format_tool_name(block.name)).classes("tau-tool-name").style(
                f"color: {name_color} !important;"
            )
            ui.label(preview).classes("flex-1 min-w-0 truncate tau-tool-preview")
            chevron_ref["el"] = ui.icon("expand_more").classes("tau-tool-chevron")

        details = ui.column().classes("w-full gap-0 tau-tool-details")
        details.set_visibility(False)
        details_container["el"] = details
        with details:
            ui.markdown(f"```json\n{json.dumps(block.args, indent=2)}\n```").classes(
                "text-xs px-2.5 py-2 tau-tool-args"
            )
            if result is not None and result.content:
                result_color = "text-[#f87171]" if is_error else "text-[var(--text-muted)]"
                ui.markdown(result.content).classes(
                    f"text-xs whitespace-pre-wrap px-2.5 py-2 {result_color} tau-tool-result"
                )
