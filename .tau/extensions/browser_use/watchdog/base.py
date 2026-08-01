"""Base class for browser monitoring components."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from browser.hooks import BrowserEvent

if TYPE_CHECKING:
    from browser.service import Browser

_log = logging.getLogger(__name__)


class BaseWatchdog:
    """Attach convention-based event handlers to a browser hook service."""

    LISTENS_TO: ClassVar[tuple[type[BrowserEvent], ...]] = ()
    EMITS: ClassVar[tuple[type[BrowserEvent], ...]] = ()

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._unsubscribers: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task[object]] = set()

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"{__name__}.{type(self).__name__}")

    def attach(self) -> None:
        if self._unsubscribers:
            raise RuntimeError(f"{type(self).__name__} is already attached")
        for event_class in self.LISTENS_TO:
            handler_name = f"on_{event_class.__name__}"
            handler = getattr(self, handler_name, None)
            if handler is None:
                raise RuntimeError(
                    f"{type(self).__name__} declares {event_class.__name__} "
                    f"but has no {handler_name} handler"
                )
            event_type = event_class().type
            self._unsubscribers.append(
                self.browser.hooks.register(event_type, handler)
            )

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()

    def create_task(self, coroutine: object, *, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.logger.error("watchdog task failed", exc_info=error)
