"""Emit browser hook events when CDP reports a target crash."""

from __future__ import annotations

from typing import Any, ClassVar

from ..browser.hooks import (
    BrowserErrorEvent,
    BrowserEvent,
    BrowserReconnectedEvent,
    BrowserStartedEvent,
    BrowserStopEvent,
    TargetCrashedEvent,
)

from .base import BaseWatchdog


class CrashWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserReconnectedEvent,
        BrowserStopEvent,
    )
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = (
        TargetCrashedEvent,
        BrowserErrorEvent,
    )

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        await self._attach()

    async def on_BrowserReconnectedEvent(
        self, _event: BrowserReconnectedEvent
    ) -> None:
        await self._attach()

    async def _attach(self) -> None:
        client = self.browser.client
        if client is not None:
            await client.target.set_discover_targets({"discover": True})
            client.register("Target.targetCrashed", self._on_crash)

    async def on_BrowserStopEvent(self, _event: BrowserStopEvent) -> None:
        if self.browser.client is not None:
            self.browser.client.unregister("Target.targetCrashed", self._on_crash)

    def _on_crash(self, params: dict[str, Any], _session_id: str | None) -> None:
        target_id = params.get("targetId", "")
        message = (
            f"target crashed with status {params.get('status', 'unknown')} "
            f"and error code {params.get('errorCode', 'unknown')}"
        )
        self.create_task(
            self._emit_crash(target_id, message),
            name=f"target-crash-{target_id}",
        )

    async def _emit_crash(self, target_id: str, message: str) -> None:
        await self.browser.hooks.emit(
            TargetCrashedEvent(target_id=target_id, error=message)
        )
        await self.browser.hooks.emit(
            BrowserErrorEvent(
                error_type="TargetCrashed",
                message=message,
                details={"target_id": target_id},
            )
        )
