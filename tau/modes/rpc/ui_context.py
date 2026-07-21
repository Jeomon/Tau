"""``ctx.ui`` for RPC mode.

Presents the same surface as the interactive ``UIContext`` so extensions do not
have to branch on the host mode, but backed by the JSON-lines protocol instead
of a terminal: dialogs and status/widget/title updates become
``extension_ui_request`` records, and everything that needs a real TUI
(components, overlays, footers, themes, raw key input) degrades to a no-op.

**Capability flag.** ``supports_components`` is ``False`` here and ``True`` on
the interactive context. Anything that renders its own ``Component`` must check
it — ``ctx.ui`` being non-``None`` only promises dialogs, not a screen to draw
on. Skipping that check is exactly how an extension ends up calling
``custom_inline()``, getting ``None`` back, and crashing on the result.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.modes.rpc.mode import RpcExtensionUIContext

_log = logging.getLogger(__name__)


class RpcUIContext:
    """The ``ctx.ui`` object extensions see in RPC mode."""

    #: No terminal to render into — see the module docstring.
    supports_components = False

    def __init__(self, bridge: RpcExtensionUIContext) -> None:
        self._bridge = bridge

    # ── Dialogs (real, over the protocol) ────────────────────────────────────

    async def select(
        self, title: str, options: list[str], timeout: float | None = None
    ) -> str | None:
        return await self._bridge.select(title, options, timeout)

    async def multi_select(
        self, title: str, options: list[str], timeout: float | None = None
    ) -> list[str] | None:
        """Pick any number of ``options``; ``[]`` means "none", ``None`` cancelled.

        Mirrors ``UIContext.multi_select``, which shows a checkbox list in the
        TUI — the two are interchangeable from an extension's point of view.
        """
        return await self._bridge.multi_select(title, options, timeout)

    async def confirm(self, title: str, message: str = "", timeout: float | None = None) -> bool:
        return await self._bridge.confirm(title, message, timeout)

    async def prompt(
        self, label: str, *, secret: bool = False, timeout: float | None = None
    ) -> str | None:
        """Single-line text entry. ``secret`` is advisory — masking is the client's call."""
        return await self._bridge.input(label, "", timeout)

    #: The protocol spells this ``input``; ``prompt`` is the interactive name.
    async def input(
        self, title: str, placeholder: str = "", timeout: float | None = None
    ) -> str | None:
        return await self._bridge.input(title, placeholder, timeout)

    async def editor(
        self, title: str, prefill: str = "", timeout: float | None = None
    ) -> str | None:
        return await self._bridge.editor(title, prefill, timeout)

    # ── Fire-and-forget surface (real, over the protocol) ────────────────────

    def notify(self, message: str | list[str], type: str = "info") -> None:  # noqa: A002
        text = "\n".join(message) if isinstance(message, list) else message
        self._bridge.notify(text, type)

    def set_status(self, key: str, text: str | None) -> None:
        self._bridge.set_status(key, text)

    def clear_status(self, key: str) -> None:
        self._bridge.set_status(key, None)

    def set_widget(
        self,
        id: str,  # noqa: A002
        widget: Any,
        placement: str = "above_editor",
    ) -> None:
        """Only line-based widgets cross the protocol; a ``Component`` is dropped."""
        if widget is not None and not isinstance(widget, list):
            _log.debug("rpc ui: component widget %r ignored (no renderer)", id)
            return
        wire_placement = "belowEditor" if placement == "below_editor" else "aboveEditor"
        self._bridge.set_widget(id, widget, wire_placement)

    def remove_widget(self, id: str) -> None:  # noqa: A002
        self._bridge.set_widget(id, None)

    def set_title(self, title: str) -> None:
        self._bridge.set_title(title)

    def set_editor_text(self, text: str) -> None:
        self._bridge.set_editor_text(text)

    def paste_to_editor(self, text: str) -> None:
        # No cursor to insert at — the client gets the text and decides.
        self._bridge.set_editor_text(text)

    # ── TUI-only surface (no-ops) ────────────────────────────────────────────
    #
    # These need a terminal grid, a layout tree, or a live editor. They return
    # neutral values rather than raising so an extension written for the TUI
    # still runs end to end under RPC — minus the decoration.

    async def custom(self, *_args: Any, **_kwargs: Any) -> None:
        """Unsupported: there is no surface to render a component onto."""
        _log.debug("rpc ui: custom() is unavailable; check ui.supports_components first")
        return None

    async def custom_inline(self, *_args: Any, **_kwargs: Any) -> None:
        """Unsupported: see :meth:`custom`."""
        _log.debug("rpc ui: custom_inline() is unavailable; check ui.supports_components first")
        return None

    def show_overlay(self, *_args: Any, **_kwargs: Any) -> object:
        _log.debug("rpc ui: show_overlay() is unavailable")

        class _NoopHandle:
            def close(self) -> None:
                return None

        return _NoopHandle()

    def set_footer(self, component_or_factory: Any) -> None:
        return None

    def restore_footer(self) -> None:
        return None

    def set_header(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_editor_component(self, factory: Any) -> None:
        return None

    def get_editor_component(self) -> None:
        return None

    def clear_messages(self) -> None:
        return None

    def set_working_message(self, msg: str | None = None) -> None:
        return None

    def set_working_visible(self, visible: bool) -> None:
        return None

    def set_working_indicator(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        return None

    def get_editor_text(self) -> str:
        return ""

    def has_active_selector(self) -> bool:
        return False

    def request_render(self) -> None:
        return None

    def on_terminal_input(self, handler: Callable[[Any], bool | None]) -> Callable[[], None]:
        """No raw key stream in RPC — returns an unsubscribe that does nothing."""
        return lambda: None

    # ── Theme / display preferences (no live view to change) ─────────────────

    @property
    def theme(self) -> None:
        return None

    def get_all_themes(self) -> list[str]:
        return []

    def set_theme(self, theme: Any, *, persist: bool = False) -> bool:
        return False

    def get_tools_expanded(self) -> bool:
        return False

    def set_tools_expanded(self, expanded: bool) -> None:
        return None

    def get_tool_results_expanded(self) -> bool:
        return False

    def set_tool_results_expanded(self, expanded: bool) -> None:
        return None
