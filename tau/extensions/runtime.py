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
        "tool_call",
        "tool_result",
        "before_provider_request",
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

    # ── Errors ────────────────────────────────────────────────────────────────

    def _record_error(self, error: ExtensionError) -> None:
        """Append an error and let the host mode observe it (RPC and the TUI show these).

        Total by construction: this is only ever called because something has
        already gone wrong, and every caller is inside an exception handler
        that another failure would escape. Escaping here would propagate out of
        the registered hook handler into ``Hooks.emit``, which logs and returns
        no result — turning a *reported* extension failure back into a silent
        one, and on ``tool_call`` into a silently permitted call.

        The append happens first so the record survives even when telling
        anyone about it does not.
        """
        self._errors.append(error)
        try:
            runtime = self.runtime_ref.runtime
            report = getattr(runtime, "report_extension_error", None)
            if callable(report):
                report(error)
        except Exception:  # noqa: BLE001 - see above; reporting must not raise
            _log.debug("could not report extension error", exc_info=True)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _make_interceptable_handler(self, ext: Extension, handler: Callable) -> Callable:
        """Return a hooks-compatible wrapper that injects ctx and propagates the return value."""

        async def wrapped(event: Any) -> Any:
            """Invoke handler with extension context."""
            return await self._invoke_handler(
                ext,
                handler,
                event,
                self._context(ext, getattr(event, "type", "unknown")),
                getattr(event, "type", "unknown"),
            )

        return wrapped

    def _context(self, ext: Any, event_type: str) -> Any:
        """Build the ``ExtensionContext``, or ``None`` if it cannot be built.

        Guarded for the same reason handler bodies are, and for a sharper
        reason on interceptable events. This runs *inside* the registered hook
        handler, so an exception escaping it lands in ``Hooks.emit``, which
        logs and moves on — the caller then collects no result at all. For
        ``tool_call`` that is indistinguishable from "no objection", so a
        context failure would execute a call the gate never got to inspect,
        and ``_record_error`` would never fire either: silent, in the one place
        silence is least affordable.

        Handing the handler ``None`` instead lets it run and decide. Most
        handlers will then fail on ``ctx.something``, which ``_invoke_handler``
        records — visible, rather than an unexplained allow.
        """
        runtime = self.runtime_ref.runtime
        if runtime is None:
            return None
        from tau.extensions.context import ExtensionContext

        try:
            return ExtensionContext.from_runtime(runtime)
        except Exception:
            tb = traceback.format_exc()
            _log.warning(
                "extension %s: could not build context for %r: %s",
                getattr(ext, "path", "?"),
                event_type,
                tb.strip().splitlines()[-1],
            )
            self._record_error(
                ExtensionError(
                    extension_path=getattr(ext, "path", "?"),
                    event=event_type,
                    error=f"could not build extension context: {tb.strip().splitlines()[-1]}",
                    stack=tb,
                )
            )
            return None

    async def _invoke_handler(
        self, ext: Any, handler: Any, event: Any, ctx: Any, event_type: str
    ) -> Any:
        """Run one extension handler, bracketed by the callback-depth counters.

        A raising handler is recorded and swallowed: one bad extension must not
        abort delivery to the others, nor let its traceback escape into the
        runtime that emitted the event. The depth counters must be balanced
        even then, hence the ``finally``.
        """
        self._callback_depth("_begin_extension_callback")
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
                event_type,
                tb.strip().splitlines()[-1],
            )
            self._record_error(
                ExtensionError(
                    extension_path=ext.path,
                    event=event_type,
                    error=tb.strip().splitlines()[-1],
                    stack=tb,
                )
            )
            return None
        finally:
            self._callback_depth("_end_extension_callback")

    def _callback_depth(self, name: str) -> None:
        """Nudge the runtime's extension-callback depth counter, never raising.

        Both the lookup and the call used to sit outside the ``try`` above, so
        a runtime that threw from either took the exception out of the
        registered hook handler and into ``Hooks.emit``, which logs it and
        collects no result — read as consent on ``tool_call``. Bookkeeping
        does not get to decide whether a tool runs.
        """
        try:
            fn = getattr(self.runtime_ref.runtime, name, None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001 - see above; must never raise
            _log.debug("extension callback counter %r failed", name, exc_info=True)

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

        for ext in self._extensions:
            handlers = ext.handlers.get(event_type, [])
            if not handlers:
                continue
            # Per-extension rather than once for the whole loop: a context
            # failure is attributed to an extension in the error record, and
            # building it lazily means an event nobody handles pays nothing.
            ctx = self._context(ext, event_type)
            for handler in handlers:
                await self._invoke_handler(ext, handler, event, ctx, event_type)

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

    def get_extensions(self) -> tuple[Extension, ...]:
        """Return loaded extensions in registration order."""
        return tuple(self._extensions)

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

    def get_markdown_transformers(self) -> list[Any]:
        """Return every markdown transformer, in extension load order."""
        result: list[Any] = []
        for ext in self._extensions:
            result.extend(ext.markdown_transformers)
        return result

    def get_autocomplete_providers(self) -> list[Any]:
        """Return all autocomplete providers registered by extensions."""
        result: list[Any] = []
        for ext in self._extensions:
            result.extend(ext.autocomplete_providers)
        return result
