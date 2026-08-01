"""Automatically load and save configured browser storage state."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import ClassVar

from browser.hooks import (
    BrowserEvent,
    BrowserStartedEvent,
    BrowserStopEvent,
    NavigationCompleteEvent,
    StorageStateLoadedEvent,
    StorageStateSavedEvent,
)

from .base import BaseWatchdog


class StorageStateWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserStopEvent,
        NavigationCompleteEvent,
    )
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = (
        StorageStateLoadedEvent,
        StorageStateSavedEvent,
    )

    def __init__(self, browser) -> None:
        super().__init__(browser)
        self._autosave_task: asyncio.Task[None] | None = None

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        path = self.browser.settings.storage_state_path
        if path is not None and Path(path).expanduser().is_file():
            await self.browser.storage.load(path)
        self._start_autosave()

    async def on_BrowserStopEvent(self, _event: BrowserStopEvent) -> None:
        await self._stop_autosave()
        path = self.browser.settings.storage_state_path
        if path is not None and self.browser.settings.auto_save_storage_state:
            await self.browser.storage.save(path)

    async def on_NavigationCompleteEvent(
        self, event: NavigationCompleteEvent
    ) -> None:
        if event.target_id:
            await self.browser.storage.apply_pending_session_storage(
                event.target_id,
                event.url,
            )

    def _start_autosave(self) -> None:
        path = self.browser.settings.storage_state_path
        interval = self.browser.settings.storage_state_autosave_interval
        if path is None or not interval or self._autosave_task is not None:
            return
        self._autosave_task = asyncio.create_task(
            self._autosave_loop(path, interval), name="storage-state-autosave"
        )

    async def _stop_autosave(self) -> None:
        task, self._autosave_task = self._autosave_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _autosave_loop(self, path: Path | str, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            if self.browser.client is None:
                continue
            try:
                await self.browser.storage.save(path)
            except Exception:
                self.logger.exception("periodic storage-state autosave failed")

