"""Auto-resolve native JavaScript dialogs so navigation never hangs."""

from __future__ import annotations

from typing import Any, ClassVar

from ..browser.hooks import BrowserEvent, BrowserReconnectedEvent, BrowserStartedEvent, BrowserStopEvent

from .base import BaseWatchdog

# alert/confirm/beforeunload have no useful text field for us to fill in, so
# accepting them is the only way to unblock the page. A prompt() dialog asks
# for input we don't have, so it must be dismissed rather than accepted.
_DISMISS_DIALOG_TYPES = {"prompt"}


class PopupsWatchdog(BaseWatchdog):
    """Respond to Page.javascriptDialogOpening before it can stall the renderer."""

    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserReconnectedEvent,
        BrowserStopEvent,
    )

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        self._attach()

    async def on_BrowserReconnectedEvent(self, _event: BrowserReconnectedEvent) -> None:
        self._attach()

    def _attach(self) -> None:
        client = self.browser.client
        if client is not None:
            client.register("Page.javascriptDialogOpening", self._on_dialog_opening)

    async def on_BrowserStopEvent(self, _event: BrowserStopEvent) -> None:
        if self.browser.client is not None:
            self.browser.client.unregister(
                "Page.javascriptDialogOpening", self._on_dialog_opening
            )

    def _on_dialog_opening(
        self, params: dict[str, Any], session_id: str | None
    ) -> None:
        self.create_task(
            self._handle_dialog(params, session_id), name="js-dialog-opening"
        )

    async def _handle_dialog(
        self, params: dict[str, Any], session_id: str | None
    ) -> None:
        client = self.browser.client
        if client is None:
            return
        dialog_type = params.get("type", "alert")
        accept = dialog_type not in _DISMISS_DIALOG_TYPES
        try:
            await client.page.handle_java_script_dialog(
                {"accept": accept}, session_id=session_id
            )
        except Exception:
            self.logger.exception("failed to resolve JavaScript dialog")
