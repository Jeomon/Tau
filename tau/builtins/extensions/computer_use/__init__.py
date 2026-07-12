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
compact desktop-state summary is injected ephemerally into LLM context at the
start of every turn, the same way the todo extension re-asserts its live
list: via the "context" hook returning
ContextEventResult(ephemeral_messages=[...]). It is never written to session
history, so it always reflects the current screen rather than a stale
snapshot from whenever the model last looked.

The `mode` setting (see manifest.json) controls what that summary contains:
"screenshot" (an image of the screen), "accessibility_tree" (the default —
focused/open windows plus accessible interactive elements, see state.py), or
"both". The screenshot is only ever included if the active model actually
accepts image input (checked fresh every turn against Modality.Image, since
/model can switch models mid-session); otherwise the accessibility tree is
used instead so the turn still gets a usable observation.
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

    mode = config.get("mode", "accessibility_tree")

    def _inject_desktop_state(_event, _ctx: ExtensionContext):
        from tau.hooks.engine import ContextEventResult
        from tau.inference.model.types import Modality

        model = getattr(_ctx.llm, "model", None)
        supports_image = model is not None and Modality.Image in model.input
        message = build_state_message(desktop, mode, supports_image)
        return ContextEventResult(ephemeral_messages=[message] if message is not None else [])

    tau.on("context", _inject_desktop_state)

    @tau.on("extension_unload")
    async def _close_desktop(_event, _ctx: ExtensionContext) -> None:
        if desktop.is_open:
            desktop.close()
