from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

# Hook events that get echoed into the page log. Mirrors the subset used by
# `_run_json` in tau/console/cli.py; kept minimal here since this is a first
# scaffold, not the final presentation layer.
_HOOK_NAMES = (
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_end",
    "settled",
)


def _describe(event: object) -> str:
    """Render a hook event as one log line."""
    message = getattr(event, "message", None)
    text = getattr(message, "text_content", None)
    if callable(text):
        return f"{type(event).__name__}: {text()}"
    return type(event).__name__


class MessageList:
    """Runtime event log for the browser chat page."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def render(self) -> None:
        """Render the message list and subscribe it to runtime hook events."""
        with ui.column().classes("w-full h-5/6 min-h-0"):
            log = ui.log().classes("w-full h-full")

        async def on_event(event: object) -> None:
            log.push(_describe(event))

        unsubs = [self._runtime.hooks.register(name, on_event) for name in _HOOK_NAMES]
        ui.context.client.on_disconnect(lambda: [unsub() for unsub in unsubs])
