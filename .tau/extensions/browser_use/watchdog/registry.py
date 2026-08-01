"""Watchdog collection management."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from .base import BaseWatchdog

if TYPE_CHECKING:
    from ..browser.service import Browser

TWatchdog = TypeVar("TWatchdog", bound=BaseWatchdog)


class WatchdogRegistry:
    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._watchdogs: list[BaseWatchdog] = []

    def add(self, watchdog_type: type[TWatchdog]) -> TWatchdog:
        watchdog = watchdog_type(self.browser)
        watchdog.attach()
        self._watchdogs.append(watchdog)
        return watchdog

    def get(self, watchdog_type: type[TWatchdog]) -> TWatchdog | None:
        for watchdog in self._watchdogs:
            if isinstance(watchdog, watchdog_type):
                return watchdog
        return None

    def detach_all(self) -> None:
        for watchdog in reversed(self._watchdogs):
            watchdog.detach()
        self._watchdogs.clear()

    def __iter__(self):
        return iter(self._watchdogs)
