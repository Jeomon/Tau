"""Grant configured browser permissions after connecting."""

from __future__ import annotations

from typing import ClassVar

from browser.hooks import BrowserEvent, BrowserReconnectedEvent, BrowserStartedEvent

from .base import BaseWatchdog


class PermissionsWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserReconnectedEvent,
    )

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        await self._grant_permissions()

    async def on_BrowserReconnectedEvent(
        self, _event: BrowserReconnectedEvent
    ) -> None:
        await self._grant_permissions()

    async def _grant_permissions(self) -> None:
        permissions = self.browser.settings.permissions
        if not permissions or self.browser.client is None:
            return
        try:
            await self.browser.client.send(
                "Browser.grantPermissions",
                {"permissions": list(permissions)},
            )
        except Exception:
            self.logger.exception("failed to grant browser permissions")
