"""computer_use — control the local desktop (macOS Accessibility API / Windows UI Automation).

Registers a single ``computer`` tool that drives the mouse, keyboard, and
application windows through a platform-neutral ``Desktop`` interface (see
``types.py``); the concrete implementation is picked at registration time by
``router.get_desktop_class()`` based on the host OS.

Disabled by default (see manifest.json) — this tool controls the real mouse
and keyboard, so it should be turned on deliberately via /settings or
extensions.list settings.

  {
    "extensions": {
      "list": [{ "path": "computer_use", "settings": { "enabled": true } }]
    }
  }

While the desktop session is open (after a computer action='open' call), a
compact desktop-state summary — focused/open windows plus the accessible
interactive elements on screen (see state.py) — is injected ephemerally into
LLM context at the start of every turn, the same way the todo extension
re-asserts its live list: via the "context" hook returning
ContextEventResult(ephemeral_messages=[...]). It is never written to session
history, so it always reflects the current screen rather than a stale
snapshot from whenever the model last looked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .computer import ComputerTool
from .router import get_desktop_class
from .state import build_state_message

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", False):
        return
    try:
        desktop = get_desktop_class()()
    except RuntimeError:
        return  # unsupported platform (e.g. Linux)

    tau.register_tool(ComputerTool(desktop))

    def _inject_desktop_state(_event, _ctx: ExtensionContext):
        text = build_state_message(desktop)
        if text is not None:
            from tau.hooks.engine import ContextEventResult
            from tau.message.types import UserMessage
            message = UserMessage.from_text(text)
            return ContextEventResult(ephemeral_messages=[message])
        return ContextEventResult(ephemeral_messages=[])

    tau.on("context", _inject_desktop_state)

    @tau.on("extension_unload")
    async def _close_desktop(_event, _ctx: ExtensionContext) -> None:
        if desktop.is_open:
            desktop.close()
