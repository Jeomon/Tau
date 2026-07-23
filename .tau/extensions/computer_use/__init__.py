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

from .router import get_desktop_class, get_platform_name

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext

    from .types import Desktop


class _LazyDesktop:
    """Duck-types ``Desktop``, deferring the real backend's import and
    construction until desktop access is actually requested.

    ``get_desktop_class()`` imports the concrete platform backend (e.g.
    ``macos.desktop``), which on macOS pulls in the PyObjC/Quartz/AppKit
    stack — measured at ~400ms, entirely on the synchronous extension-load
    path that gates the TUI becoming interactive. Most sessions never touch
    the (opt-in, disabled-by-default) computer tool at all, so that cost
    shouldn't be paid at startup for a tool that might never be used. This
    proxy pays it on first real use (``action='open'``) instead.

    Not a ``Desktop`` subclass: ``Desktop`` is an ``ABC`` whose abstract
    methods must be satisfied at class-definition time, which would defeat
    the point of deferring the import. ``computer.py``/``state.py`` only
    ever call attributes present here or forwarded via ``__getattr__``.
    """

    def __init__(self) -> None:
        self._impl: Desktop | None = None

    def _ensure(self) -> Desktop:
        if self._impl is None:
            self._impl = get_desktop_class()()
        return self._impl

    @property
    def is_open(self) -> bool:
        # False without constructing the real backend — lets the per-turn
        # context hook (state.py) and startup stay cheap for sessions that
        # never call action='open'.
        return self._impl is not None and self._impl.is_open

    def open(self) -> None:
        self._ensure().open()

    def close(self) -> None:
        if self._impl is not None:
            self._impl.close()

    def __getattr__(self, name: str):
        return getattr(self._ensure(), name)


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", False):
        return
    try:
        # Cheap platform check only — validates OS support (same as before)
        # without importing the heavy concrete backend.
        get_platform_name()
    except RuntimeError:
        return  # unsupported platform (e.g. Linux)

    # Deferred until here rather than at module scope: building
    # ComputerTool's pydantic schema (ComputerSchema — a dozen-plus fields
    # and several enums) has real, measurable cost (tens of ms) even before
    # any desktop backend is touched. Disabled by default (see
    # manifest.json), so most installs return above and should never pay
    # this — but the extension *file* itself is always imported by the
    # loader to find `register()`, so anything at module scope is paid
    # unconditionally regardless of the enabled check.
    from .tool import ComputerTool
    from .state import build_state_message

    desktop = _LazyDesktop()

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
