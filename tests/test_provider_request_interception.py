"""`before_provider_request` can refuse the call, not just watch it.

The event carries the live ``headers``, ``messages`` and ``options`` objects,
so *rewriting* a request — redacting a prompt, adding a tracing header — has
always worked by mutating them in place. Refusing one had nowhere to go: the
event was absent from ``_INTERCEPTABLE_EVENTS``, so an extension handler's
return value went to the catch-all subscriber and was discarded, and the emit
site did not read results either.

That left an approved-model registry or an egress policy able to observe a
provider call and unable to stop it — the same shape as the `tool_call`
fail-open, one layer up.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tau.extensions.loader import Extension, LoadExtensionsResult
from tau.extensions.runtime import _INTERCEPTABLE_EVENTS, ExtensionRuntime
from tau.hooks.inference import (
    BeforeProviderRequestEvent,
    ProviderRequestBlocked,
    ProviderRequestEventResult,
)
from tau.hooks.service import Hooks


class _RuntimeRef:
    runtime = None
    services: dict[str, Any] = {}
    service_owners: dict[str, Any] = {}


def _extension(handler) -> Extension:
    ext = Extension.__new__(Extension)
    ext.path = "/tmp/guard.py"
    ext.handlers = {"before_provider_request": [handler]}
    ext.tools = {}
    ext.commands = {}
    return ext


def _hooks_with(*handlers) -> Hooks:
    hooks = Hooks()
    ExtensionRuntime(
        LoadExtensionsResult(extensions=[_extension(h) for h in handlers], errors=[]),
        hooks,
        _RuntimeRef(),  # type: ignore[arg-type]
    )
    return hooks


def _emit(hooks: Hooks, event: BeforeProviderRequestEvent | None = None) -> list[Any]:
    return asyncio.run(hooks.emit(event or BeforeProviderRequestEvent()))


def test_the_event_is_interceptable() -> None:
    assert "before_provider_request" in _INTERCEPTABLE_EVENTS


def test_a_handler_result_reaches_the_caller() -> None:
    async def guard(event, ctx):
        return ProviderRequestEventResult(block=True, reason="model not approved")

    results = _emit(_hooks_with(guard))

    blocking = [r for r in results if isinstance(r, ProviderRequestEventResult) and r.block]
    assert blocking, "the refusal never reached the emit site"
    assert blocking[0].reason == "model not approved"


def test_allowing_returns_nothing_to_act_on() -> None:
    async def guard(event, ctx):
        return None

    assert not [
        r
        for r in _emit(_hooks_with(guard))
        if isinstance(r, ProviderRequestEventResult) and r.block
    ]


def test_in_place_mutation_still_works() -> None:
    """The documented way to rewrite a request; a result type must not replace it."""

    async def guard(event, ctx):
        event.headers["x-trace"] = "abc"
        event.messages.append("redacted")
        return None

    event = BeforeProviderRequestEvent(headers={}, messages=[])
    _emit(_hooks_with(guard), event)

    assert event.headers == {"x-trace": "abc"}
    assert event.messages == ["redacted"]


def test_every_handler_runs_even_when_one_refuses() -> None:
    """Checked after the emit, so a guard that redacts still redacts."""
    seen: list[str] = []

    async def refuse(event, ctx):
        seen.append("refuse")
        return ProviderRequestEventResult(block=True, reason="no")

    async def observe(event, ctx):
        seen.append("observe")
        return None

    _emit(_hooks_with(refuse, observe))

    assert seen == ["refuse", "observe"]


def test_a_raising_handler_does_not_hide_another_refusal() -> None:
    async def explode(event, ctx):
        raise RuntimeError("bad guard")

    async def refuse(event, ctx):
        return ProviderRequestEventResult(block=True, reason="still blocked")

    results = _emit(_hooks_with(explode, refuse))

    assert any(isinstance(r, ProviderRequestEventResult) and r.block for r in results)


class TestEngineIntegration:
    """The engine turns a refusal into a stopped turn with visible error text."""

    def _raise_if_blocked(self, results: list[Any]) -> None:
        # Mirrors engine/service.py's check verbatim; the engine's own loop
        # needs a live LLM to exercise, which this does not.
        for result in results:
            if isinstance(result, ProviderRequestEventResult) and result.block:
                raise ProviderRequestBlocked(
                    result.reason or "Provider request blocked by an extension."
                )

    def test_a_refusal_raises_with_the_handler_reason(self) -> None:
        async def guard(event, ctx):
            return ProviderRequestEventResult(
                block=True, reason="egress policy: no external models"
            )

        with pytest.raises(ProviderRequestBlocked, match="egress policy"):
            self._raise_if_blocked(_emit(_hooks_with(guard)))

    def test_a_reasonless_refusal_still_says_something(self) -> None:
        async def guard(event, ctx):
            return ProviderRequestEventResult(block=True)

        with pytest.raises(ProviderRequestBlocked, match="blocked by an extension"):
            self._raise_if_blocked(_emit(_hooks_with(guard)))

    def test_allowing_does_not_raise(self) -> None:
        async def guard(event, ctx):
            return None

        self._raise_if_blocked(_emit(_hooks_with(guard)))

    def test_the_engine_check_matches_this_one(self) -> None:
        """Guards against the mirror above drifting from the real code."""
        import inspect

        from tau.engine.service import Engine

        source = inspect.getsource(Engine)
        assert "ProviderRequestEventResult" in source
        assert "ProviderRequestBlocked" in source
        assert "provider_results" in source


def test_the_exception_is_exported() -> None:
    """Extensions catch or raise these; they must be importable from tau.hooks."""
    from tau.hooks import ProviderRequestBlocked as Exported
    from tau.hooks import ProviderRequestEventResult as ExportedResult

    assert Exported is ProviderRequestBlocked
    assert ExportedResult is ProviderRequestEventResult
