"""Shared asynchronous hook registry."""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from .types import HookEvent

_log = logging.getLogger(__name__)

HookHandler: TypeAlias = Callable[["HookEvent"], Awaitable[Any] | Any]
Unsubscribe: TypeAlias = Callable[[], None]


class Hooks:
    """Register and emit typed application hooks."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)
        self._subscribers: list[HookHandler] = []

    def register(self, event_type: str, handler: HookHandler) -> Unsubscribe:
        self._handlers[event_type].append(handler)
        return lambda: self.unregister(event_type, handler)

    def unregister(self, event_type: str, handler: HookHandler) -> None:
        with contextlib.suppress(ValueError):
            self._handlers[event_type].remove(handler)

    def subscribe(self, listener: HookHandler) -> Unsubscribe:
        self._subscribers.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: HookHandler) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(listener)

    def on(self, event_type: str) -> Callable[[HookHandler], HookHandler]:
        def decorator(handler: HookHandler) -> HookHandler:
            self.register(event_type, handler)
            return handler

        return decorator

    async def emit(self, event: Any) -> list[Any]:
        results: list[Any] = []
        for handler in list(self._handlers.get(event.type, ())):
            result, succeeded = await self._invoke(handler, event)
            if succeeded:
                results.append(result)

        for subscriber in list(self._subscribers):
            await self._invoke(subscriber, event)
        return results

    async def _invoke(
        self, handler: HookHandler, event: Any
    ) -> tuple[Any, bool]:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                result = await result
            return result, True
        except Exception:
            _log.exception(
                "hook %r failed for event %r",
                getattr(handler, "__name__", handler),
                event.type,
            )
            return None, False

    def handler_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, ()))

    def registered_events(self) -> list[str]:
        return [event_type for event_type, handlers in self._handlers.items() if handlers]

    def clear(self, event_type: str | None = None) -> None:
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)

