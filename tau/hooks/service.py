from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from tau.hooks.types import HookEvent

_log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[Any] | Any]
Unsubscribe = Callable[[], None]


class Hooks:
    """Register and emit typed lifecycle hooks.

    Usage — imperative:
        hooks = Hooks()
        hooks.register('agent_start', my_handler)
        hooks.unregister('agent_start', my_handler)

    Usage — decorator:
        @hooks.on('agent_start')
        async def handle(event: AgentStartEvent) -> None:
            ...

    Usage — emit:
        results = await hooks.emit(AgentStartEvent())
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._subscribers: list[Handler] = []

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, event_type: str, handler: Handler) -> Unsubscribe:
        """Register a handler for the given event type. Returns an unsubscribe callable."""
        self._handlers[event_type].append(handler)
        return lambda: self.unregister(event_type, handler)

    def unregister(self, event_type: str, handler: Handler) -> None:
        """Remove a previously registered handler. No-op if not found."""
        with contextlib.suppress(ValueError):
            self._handlers[event_type].remove(handler)

    def subscribe(self, listener: Handler) -> Unsubscribe:
        """Register a catch-all listener that receives every emitted event."""
        self._subscribers.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Handler) -> None:
        """Remove a previously registered listener. Idempotent."""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(listener)

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        """Decorator for registering a handler for an event type."""

        def decorator(fn: Handler) -> Handler:
            self.register(event_type, fn)
            return fn

        return decorator

    # ── Emit ──────────────────────────────────────────────────────────────────

    async def emit(self, event: HookEvent, *, timeout: float | None = None) -> list[Any]:
        """Fire all handlers and subscribers registered for an event type.

        ``timeout`` bounds each individual handler/subscriber await (not the
        whole emit). Pass it for shutdown-path events (e.g. ``TuiExitEvent``,
        ``RuntimeStopEvent``) so one slow or hung extension handler can't stall
        the whole exit — a timed-out handler is logged and skipped, not retried.
        Left unset (the default) for normal lifecycle events, which may
        legitimately need more time to run.
        """
        event_type: str = event.type  # type: ignore[attr-defined]
        handlers = list(self._handlers.get(event_type, []))
        results: list[Any] = []

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    result = (
                        await asyncio.wait_for(result, timeout)
                        if timeout is not None
                        else await result
                    )
                results.append(result)
            except TimeoutError:
                _log.warning(
                    "Hook handler %r timed out after %.1fs on event %r; skipping",
                    getattr(handler, "__name__", handler),
                    timeout,
                    event_type,
                )
            except Exception:
                _log.error(
                    "Hook handler %r raised on event %r:\n%s",
                    getattr(handler, "__name__", handler),
                    event_type,
                    traceback.format_exc(),
                )

        for subscriber in list(self._subscribers):
            try:
                result = subscriber(event)
                if asyncio.iscoroutine(result):
                    if timeout is not None:
                        await asyncio.wait_for(result, timeout)
                    else:
                        await result
            except TimeoutError:
                _log.warning(
                    "Hook subscriber %r timed out after %.1fs on event %r; skipping",
                    getattr(subscriber, "__name__", subscriber),
                    timeout,
                    event_type,
                )
            except Exception:
                _log.error(
                    "Hook subscriber %r raised on event %r:\n%s",
                    getattr(subscriber, "__name__", subscriber),
                    event_type,
                    traceback.format_exc(),
                )

        return results

    # ── Introspection ─────────────────────────────────────────────────────────

    def handler_count(self, event_type: str) -> int:
        """Return the number of handlers registered for an event type."""
        return len(self._handlers.get(event_type, []))

    def registered_events(self) -> list[str]:
        """Return event types that have at least one handler."""
        return [k for k, v in self._handlers.items() if v]

    def clear(self, event_type: str | None = None) -> None:
        """Clear all handlers for an event type, or all handlers if None."""
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)
