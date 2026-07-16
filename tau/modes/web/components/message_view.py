from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nicegui import ui

MessageRole = Literal["assistant", "user"]


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

    def __init__(self, text: str, *, role: MessageRole) -> None:
        self._text = text
        self._role = role

    def render(self) -> RenderedMessage:
        """Render the message into the current NiceGUI container."""
        root = ui.column().classes(f"w-full {self._alignment_class()}")
        with root, ui.card().classes(self._bubble_classes()):
            content = ui.markdown(self._text).classes("max-w-none text-sm")
        return RenderedMessage(root=root, content=content)

    def _alignment_class(self) -> str:
        return "items-end" if self._role == "user" else "items-start"

    def _bubble_classes(self) -> str:
        base = "max-w-3xl rounded-2xl p-4 shadow-sm"
        if self._role == "user":
            return f"{base} bg-blue-600 text-white"
        return f"{base} bg-white text-slate-900 border border-slate-200"
