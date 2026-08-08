"""Collections that a long-running session would otherwise grow without limit.

Both cases here were found by auditing every container that is appended to but
never pruned. Neither is a cycle or a missing `close()` — Python's collector
handles those — they are records kept forever because nothing said to stop.

The shape to watch for is a per-occurrence record on a per-occurrence event:
bounded input makes an unbounded list look fine right up until the input is a
streaming chunk or a whole session's typing.
"""

from __future__ import annotations

import pytest

from tau.extensions.api import ExtensionError
from tau.extensions.loader import Extension, LoadExtensionsResult
from tau.extensions.runtime import _MAX_DISPATCH_ERRORS, ExtensionRuntime
from tau.hooks.service import Hooks


def _extension(handlers: dict[str, list]) -> Extension:
    ext = Extension.__new__(Extension)
    ext.path = "/tmp/broken_ext.py"
    ext.handlers = handlers
    ext.tools = {}
    ext.commands = {}
    return ext


class _RuntimeRef:
    runtime = None
    services: dict = {}
    service_owners: dict = {}


class _Event:
    def __init__(self, type_name: str) -> None:
        self.type = type_name


class TestExtensionDispatchErrors:
    """A handler that raises on a streaming event must not grow the record.

    `message_update` fires once per chunk, so a broken handler recorded a full
    traceback per chunk: 5000 chunks measured at 2.6 MiB, and nothing ever
    cleared it.
    """

    def _runtime_with_broken_handler(self, event_type: str, load_errors=()):
        def boom(event, ctx):
            raise RuntimeError("handler is broken")

        hooks = Hooks()
        runtime = ExtensionRuntime(
            LoadExtensionsResult(
                extensions=[_extension({event_type: [boom]})], errors=list(load_errors)
            ),
            hooks,
            _RuntimeRef(),  # type: ignore[arg-type]
        )
        return hooks, runtime

    async def _fire(self, hooks: Hooks, event_type: str, times: int) -> None:
        for _ in range(times):
            await hooks.emit(_Event(event_type))

    @pytest.mark.asyncio
    async def test_dispatch_errors_stop_at_the_cap(self):
        hooks, runtime = self._runtime_with_broken_handler("message_update")

        await self._fire(hooks, "message_update", _MAX_DISPATCH_ERRORS * 3)

        assert len(runtime.errors) == _MAX_DISPATCH_ERRORS

    @pytest.mark.asyncio
    async def test_the_most_recent_errors_are_the_ones_kept(self):
        """Oldest-out: what the extension is doing *now* is the useful part."""
        hooks, runtime = self._runtime_with_broken_handler("message_update")

        await self._fire(hooks, "message_update", _MAX_DISPATCH_ERRORS + 5)

        assert all(e.event == "message_update" for e in runtime.errors)
        assert len(runtime.errors) == _MAX_DISPATCH_ERRORS

    @pytest.mark.asyncio
    async def test_a_flood_cannot_evict_a_load_error(self):
        """A load error names an extension that is not running at all, which
        stays true however much noise a different extension makes."""
        load_error = ExtensionError(
            extension_path="/tmp/never_loaded.py",
            event="load",
            error="ImportError: no such module",
            stack="",
        )
        hooks, runtime = self._runtime_with_broken_handler(
            "message_update", load_errors=[load_error]
        )

        await self._fire(hooks, "message_update", _MAX_DISPATCH_ERRORS * 2)

        assert runtime.errors[0] is load_error
        assert len(runtime.errors) == _MAX_DISPATCH_ERRORS + 1

    @pytest.mark.asyncio
    async def test_errors_are_still_reported_when_few(self):
        """Bounding must not cost the ordinary case its diagnostics."""
        hooks, runtime = self._runtime_with_broken_handler("session_start")

        await self._fire(hooks, "session_start", 3)

        assert len(runtime.errors) == 3
        assert "handler is broken" in runtime.errors[0].error


class TestInputHistory:
    """Submitted prompts were kept for the life of the session.

    `save_history` writes only the last 500 and `replace_history` loads at most
    that many, so the cap already existed either side of the live list — the
    list itself simply had none, and a prompt can be a multi-kilobyte paste.
    """

    def _input(self):
        from tau.tui.components.text_input import TextInput

        return TextInput()

    def test_history_stops_at_the_limit(self):
        field = self._input()

        for index in range(field._history_limit * 2):
            field.set_text(f"prompt {index}")
            field.submit()

        assert len(field._history) == field._history_limit

    def test_the_most_recent_prompts_survive(self):
        field = self._input()
        limit = field._history_limit

        for index in range(limit + 10):
            field.set_text(f"prompt {index}")
            field.submit()

        assert field._history[-1] == f"prompt {limit + 9}"
        assert field._history[0] == "prompt 10"

    def test_a_short_session_keeps_everything(self):
        field = self._input()

        for index in range(5):
            field.set_text(f"prompt {index}")
            field.submit()

        assert len(field._history) == 5

    def test_replace_history_defaults_to_the_same_limit(self):
        """One bound, not two literals that can drift apart."""
        field = self._input()

        field.replace_history([f"old {i}" for i in range(field._history_limit * 2)])

        assert len(field._history) == field._history_limit
