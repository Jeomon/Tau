"""Keep one blank tab alive so the browser process never runs out of tabs."""

from __future__ import annotations

from typing import ClassVar

from ..browser.hooks import BrowserEvent, TabClosedEvent

from .base import BaseWatchdog


class AboutBlankWatchdog(BaseWatchdog):
    """Open a fresh about:blank tab whenever the last open tab is closed.

    Closing a browser's final tab can terminate the whole Chromium process
    unexpectedly. This is opt-in (BrowserSettings.keep_one_blank_tab_alive)
    since some callers deliberately close every tab as part of shutdown.
    """

    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = (TabClosedEvent,)

    async def on_TabClosedEvent(self, _event: TabClosedEvent) -> None:
        if not self.browser.settings.keep_one_blank_tab_alive:
            return
        if self.browser.client is None or self.browser.session.pages():
            return
        try:
            await self.browser.new_page("about:blank")
        except Exception:
            self.logger.exception("failed to keep a blank tab alive")
