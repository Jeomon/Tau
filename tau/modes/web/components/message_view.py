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
        with root, ui.element("div").classes(self._bubble_classes()):
            content = ui.markdown(self._text).classes("max-w-none text-sm text-[var(--text)]")
        return RenderedMessage(root=root, content=content)

    def _alignment_class(self) -> str:
        return "items-end" if self._role == "user" else "items-start"

    def _bubble_classes(self) -> str:
        if self._role == "user":
            return "max-w-[85%] px-3 py-2 tau-bubble-user"
        return "w-full px-0 py-0 tau-bubble-assistant"
