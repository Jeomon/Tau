from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class EventBus:
    """
    Lightweight pub/sub bus for cross-extension communication.

    Separate from the main hook bus — intended for extensions to send
    signals to each other rather than to the core runtime.

    Usage:
        # Publisher
        await tau.events.emit("my-ext:done", {"count": 42})

        # Subscriber (in another extension or same one)
        @tau.events.on("my-ext:done")
        async def handler(data):
            print(data["count"])
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable | None = None) -> Any:
        """Subscribe to a named event.

        Direct call:
            tau.events.on("my-ext:done", handler)

        Decorator:
            @tau.events.on("my-ext:done")
            async def handler(data): ...
        """
        if handler is not None:
            self._handlers[event].append(handler)
            return lambda: self._handlers[event].remove(handler)

        def decorator(fn: Callable) -> Callable:
            """Register the function as an event handler."""
            self._handlers[event].append(fn)
            return fn

        return decorator

    async def emit(self, event: str, data: Any = None) -> list[Any]:
        """Fire all handlers subscribed to *event*. Returns collected results."""
        results: list[Any] = []
        for handler in list(self._handlers.get(event, [])):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            except Exception:
                _log.exception("EventBus handler %r raised on event %r", handler, event)
        return results

    def clear(self, event: str | None = None) -> None:
        """Remove all handlers for one event, or all events if None."""
        if event is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event, None)
