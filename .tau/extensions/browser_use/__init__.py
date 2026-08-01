"""browser_use — drive a Chromium browser through the Browser project's CDP stack.

Registers a single ``browser`` tool backed by this repository's ``src``
packages (Browser service, DOM interactive-element filtering, watchdogs).
The browser is not launched at load time — the first ``browser`` call with
action='open' either launches an agent-owned Chromium or attaches to an
existing Chrome via the ``cdp_url`` setting.

While the browser session is open, a fresh page observation is injected
ephemerally into LLM context at the start of every turn via the "context"
hook returning ContextEventResult(ephemeral_messages=[...]). It is never
written to session history, so the model always sees the current page rather
than a stale snapshot from whenever it last looked.

The ``mode`` setting (see manifest.json) controls what that observation
contains: "screenshot" (labeled bounding boxes over clickable elements),
"accessibility_tree" (the interactive elements from src/dom rendered as an
indented accessibility tree — role, name, and states, referenced by
element_id), or "both" (the default). The screenshot is only included if the
active model actually accepts image input (checked fresh every turn against
Modality.Image, since /model can switch models mid-session); otherwise the
accessibility tree is used instead so the turn still gets a usable
observation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    # Deferred rather than at module scope: building BrowserTool's pydantic
    # schema has real cost, and the loader imports this file unconditionally
    # to find `register()` even when the extension is disabled.
    from .session import BrowserSession
    from .tool import BrowserTool

    session = BrowserSession(
        headless=bool(config.get("headless", False)),
        cdp_url=(config.get("cdp_url") or "").strip() or None,
        highlight=bool(config.get("highlight", True)),
        user_data_dir=(config.get("user_data_dir") or "").strip() or None,
        stealth=bool(config.get("stealth", False)),
    )

    tau.register_tool(BrowserTool(session))

    mode = config.get("mode", "both")

    async def _inject_browser_state(_event, ctx: ExtensionContext):
        from tau.hooks import ContextEventResult
        from tau.inference.model.types import Modality

        from .state import build_state_message

        model = getattr(ctx.llm, "model", None)
        supports_image = model is not None and Modality.Image in model.input
        message = await build_state_message(session, mode, supports_image)
        return ContextEventResult(ephemeral_messages=[message] if message is not None else [])

    tau.on("context", _inject_browser_state)

    @tau.on("extension_unload")
    async def _close_browser(_event, _ctx: ExtensionContext) -> None:
        if session.is_open:
            await session.close()
