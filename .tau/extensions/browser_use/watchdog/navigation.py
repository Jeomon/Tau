"""Translate CDP frame loading events into navigation hooks."""

from __future__ import annotations

from typing import Any, ClassVar

from browser.hooks import (
    BrowserEvent,
    BrowserReconnectedEvent,
    BrowserStartedEvent,
    BrowserStopEvent,
    NavigationCompleteEvent,
)

from .base import BaseWatchdog


class NavigationWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (
        BrowserStartedEvent,
        BrowserReconnectedEvent,
        BrowserStopEvent,
    )
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = (NavigationCompleteEvent,)

    def __init__(self, browser) -> None:
        super().__init__(browser)
        self._urls_by_session: dict[str, str] = {}

    async def on_BrowserStartedEvent(self, _event: BrowserStartedEvent) -> None:
        self._attach()

    async def on_BrowserReconnectedEvent(
        self, _event: BrowserReconnectedEvent
    ) -> None:
        self._attach()

    def _attach(self) -> None:
        client = self.browser.client
        if client is None:
            return
        client.register("Page.frameNavigated", self._on_frame_navigated)
        client.register("Page.frameStoppedLoading", self._on_frame_stopped)

    async def on_BrowserStopEvent(self, _event: BrowserStopEvent) -> None:
        client = self.browser.client
        if client is not None:
            client.unregister("Page.frameNavigated", self._on_frame_navigated)
            client.unregister("Page.frameStoppedLoading", self._on_frame_stopped)
        self._urls_by_session.clear()

    def _on_frame_navigated(
        self, params: dict[str, Any], session_id: str | None
    ) -> None:
        frame = params.get("frame", {})
        if session_id and "parentId" not in frame:
            self._urls_by_session[session_id] = frame.get("url", "")

    def _on_frame_stopped(
        self, _params: dict[str, Any], session_id: str | None
    ) -> None:
        if session_id is None or session_id not in self._urls_by_session:
            return
        url = self._urls_by_session.pop(session_id)
        # SecurityWatchdog.on_NavigationCompleteEvent (and StorageStateWatchdog)
        # both bail out early when target_id is empty, so this must be
        # populated for the event to actually do anything.
        target_id = self.browser.session.target_for_session(session_id) or ""
        self.create_task(
            self.browser.hooks.emit(
                NavigationCompleteEvent(
                    target_id=target_id, url=url, loading_status="complete"
                )
            ),
            name="navigation-complete",
        )
