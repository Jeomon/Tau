from __future__ import annotations

import inspect
import logging
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.extensions.api import (
    Extension,
    ExtensionError,
    LoadExtensionsResult,
    ShortcutRegistration,
    _RuntimeRef,
)

if TYPE_CHECKING:
    from tau.commands.types import CommandInfo
    from tau.hooks.service import Hooks
    from tau.tool.types import Tool

_log = logging.getLogger(__name__)


# Events where handler return values matter for interception.
# These are registered directly on the hooks bus (not via the catch-all subscriber)
# so that Hooks.emit() collects their results.
_INTERCEPTABLE_EVENTS: frozenset[str] = frozenset(
    {
        "before_compaction",
        "session_before_tree",
        "user_terminal",
        "resources_discover",
        "project_trust",
        "input",
        "tool_result",
        "context",
    }
)


class ExtensionRuntime:
    """
    Owns all loaded extensions and dispatches lifecycle events to their handlers.

    Subscribed as a catch-all listener on the hooks bus so that every event
    emitted by the agent or runtime automatically reaches extension handlers —
    no changes to the agent or engine are required.

    Each handler is always called as ``handler(event, ctx)`` where ``ctx`` is a
    fresh ``ExtensionContext`` snapshot built from the live runtime at dispatch
    time.  Handler exceptions are caught per-handler and appended to ``errors``.

    Interceptable events (``_INTERCEPTABLE_EVENTS``) are registered directly on
    the hooks bus rather than going through the catch-all subscriber, so their
    return values are collected by ``Hooks.emit()`` and available for inspection
    by the caller (e.g. ``before_compaction`` handlers that return
    ``BeforeCompactionResult``).
    """

    def __init__(
        self,
        load_result: LoadExtensionsResult,
        hooks: Hooks,
        runtime_ref: _RuntimeRef,
    ) -> None:
        self._extensions: list[Extension] = load_result.extensions
        self._errors: list[ExtensionError] = list(load_result.errors)
        self.runtime_ref: _RuntimeRef = runtime_ref
        self._unsub = hooks.subscribe(self._dispatch)

        # Register interceptable handlers directly so their results flow back
        # through Hooks.emit() rather than being discarded by the subscriber path.
        self._interceptable_unsubs: list[Callable[[], None]] = []
        for ext in self._extensions:
            for event_type in _INTERCEPTABLE_EVENTS:
                for handler in ext.handlers.get(event_type, []):
                    wrapped = self._make_interceptable_handler(ext, handler)
                    self._interceptable_unsubs.append(hooks.register(event_type, wrapped))

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _make_interceptable_handler(self, ext: Extension, handler: Callable) -> Callable:
        """Return a hooks-compatible wrapper that injects ctx and propagates the return value."""

        async def wrapped(event: Any) -> Any:
            """Invoke handler with extension context."""
            from tau.extensions.context import ExtensionContext

            runtime = self.runtime_ref.runtime
            ctx = ExtensionContext.from_runtime(runtime) if runtime is not None else None
            begin = getattr(runtime, "_begin_extension_callback", None)
            end = getattr(runtime, "_end_extension_callback", None)
            if callable(begin):
                begin()
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception:
                tb = traceback.format_exc()
                _log.warning(
                    "extension %s handler for %r raised: %s",
                    ext.path,
                    getattr(event, "type", "unknown"),
                    tb.strip().splitlines()[-1],
                )
                self._errors.append(
                    ExtensionError(
                        extension_path=ext.path,
                        event=getattr(event, "type", "unknown"),
                        error=tb.strip().splitlines()[-1],
                        stack=tb,
                    )
                )
                return None
            finally:
                if callable(end):
                    end()

        return wrapped

    async def _dispatch(self, event: Any) -> None:
        """Catch-all hooks subscriber — re-dispatches every event to extension handlers.

        Interceptable events are skipped here; they are already handled by
        directly-registered hooks so their return values reach Hooks.emit().
        """
        event_type: str | None = getattr(event, "type", None)
        if not event_type:
            return
        if event_type in _INTERCEPTABLE_EVENTS:
            return

        from tau.extensions.context import ExtensionContext

        runtime = self.runtime_ref.runtime
        ctx = ExtensionContext.from_runtime(runtime) if runtime is not None else None

        for ext in self._extensions:
            for handler in ext.handlers.get(event_type, []):
                begin = getattr(runtime, "_begin_extension_callback", None)
                end = getattr(runtime, "_end_extension_callback", None)
                if callable(begin):
                    begin()
                try:
                    result = handler(event, ctx)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    tb = traceback.format_exc()
                    _log.warning(
                        "extension %s handler for %r raised: %s",
                        ext.path,
                        event_type,
                        tb.strip().splitlines()[-1],
                    )
                    self._errors.append(
                        ExtensionError(
                            extension_path=ext.path,
                            event=event_type,
                            error=tb.strip().splitlines()[-1],
                            stack=tb,
                        )
                    )
                finally:
                    if callable(end):
                        end()

    def unsubscribe(self) -> None:
        """Detach from the hooks bus (called before hot-reload replaces this runtime)."""
        self._unsub()
        for unsub in self._interceptable_unsubs:
            unsub()
        self._interceptable_unsubs.clear()

    # ── Errors ────────────────────────────────────────────────────────────────

    @property
    def errors(self) -> list[ExtensionError]:
        """All accumulated load and dispatch errors."""
        return self._errors

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_tools(self) -> list[Tool]:
        """Return all tools registered by extensions (last-writer-wins on name)."""
        tools: dict[str, Any] = {}
        for ext in self._extensions:
            tools.update(ext.tools)
        return list(tools.values())

    def get_commands(self) -> list[CommandInfo]:
        """Return all slash commands registered by extensions (last-writer-wins on name)."""
        commands: dict[str, Any] = {}
        for ext in self._extensions:
            commands.update(ext.commands)
        return list(commands.values())

    def get_shortcuts(self) -> list[ShortcutRegistration]:
        """Return all keyboard shortcuts registered by extensions."""
        result: list[ShortcutRegistration] = []
        for ext in self._extensions:
            result.extend(ext.shortcuts)
        return result

    def get_prompt_appends(self) -> list[str]:
        """Return all system-prompt additions registered by extensions."""
        result: list[str] = []
        for ext in self._extensions:
            result.extend(ext.prompt_appends)
        return result

    def get_message_renderers(self) -> dict[str, Any]:
        """Return merged message renderer registry (last-registered wins per type)."""
        result: dict[str, Any] = {}
        for ext in self._extensions:
            result.update(ext.message_renderers)
        return result

    def get_autocomplete_providers(self) -> list[Any]:
        """Return all autocomplete providers registered by extensions."""
        result: list[Any] = []
        for ext in self._extensions:
            result.extend(ext.autocomplete_providers)
        return result
