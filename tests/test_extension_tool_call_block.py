"""A `tool_call` handler registered by a real extension must be able to block.

Every existing test for this hook registers straight onto the ``Hooks`` bus.
An extension cannot do that — it calls ``tau.on("tool_call")``, which stores the
handler on the ``Extension`` and leaves ``ExtensionRuntime`` to bridge it. That
bridge has two paths:

* events in ``_INTERCEPTABLE_EVENTS`` are registered as real hook *handlers*,
  so ``Hooks.emit()`` collects what they return;
* everything else goes through the catch-all *subscriber*, which awaits the
  handler and drops its return value on the floor.

``tool_call`` sat on the wrong side of that split. A permission gate would
prompt, the user would choose Deny, the decision would be recorded — and the
call would run anyway, because the block never reached the caller. Nothing
failed loudly; the hook simply failed open, which is the worst direction for
the one event that exists to stop a tool.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tau.agent.service import Agent
from tau.extensions.loader import Extension, LoadExtensionsResult
from tau.extensions.runtime import _INTERCEPTABLE_EVENTS, ExtensionRuntime
from tau.hooks.engine import ToolCallEventResult
from tau.hooks.service import Hooks
from tau.message.types import ToolResultContent
from tau.tool.types import ToolInvocation


def _extension(handlers: dict[str, list]) -> Extension:
    ext = Extension.__new__(Extension)
    ext.path = "/tmp/fake_ext.py"
    ext.handlers = handlers
    ext.tools = {}
    ext.commands = {}
    return ext


def _runtime_with(handler, event_type: str = "tool_call") -> Hooks:
    """Wire one extension handler through the real ExtensionRuntime bridge."""
    hooks = Hooks()
    ExtensionRuntime(
        LoadExtensionsResult(extensions=[_extension({event_type: [handler]})], errors=[]),
        hooks,
        _RuntimeRef(),
    )
    return hooks


class _RuntimeRef:
    runtime = None
    services: dict[str, Any] = {}
    service_owners: dict[str, Any] = {}


def _agent(hooks: Hooks) -> Any:
    agent = Agent.__new__(Agent)
    agent.hooks = hooks  # type: ignore[attr-defined]
    return agent


def _invocation() -> ToolInvocation:
    return ToolInvocation(id="call-1", name="terminal", cwd=Path("."), params={"cmd": "rm -rf /"})


def test_tool_call_is_interceptable() -> None:
    """The whole bug in one line: it was listed for tool_result but not tool_call."""
    assert "tool_call" in _INTERCEPTABLE_EVENTS
    assert "tool_result" in _INTERCEPTABLE_EVENTS


def test_an_extension_handler_can_block_a_tool_call() -> None:
    async def gate(event, ctx):
        return ToolCallEventResult(block=True, reason="Denied by the user.")

    result = asyncio.run(_agent(_runtime_with(gate))._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent), "the block never reached the caller"
    assert result.is_error is True
    assert result.content == "Denied by the user."
    assert result.metadata.get("blocked_by_extension") is True


def test_an_extension_handler_can_rewrite_params() -> None:
    async def gate(event, ctx):
        return ToolCallEventResult(params={"cmd": "ls -la"})

    result = asyncio.run(_agent(_runtime_with(gate))._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolInvocation)
    assert result.params == {"cmd": "ls -la"}


def test_allowing_leaves_the_invocation_untouched() -> None:
    async def gate(event, ctx):
        return None

    invocation = _invocation()
    result = asyncio.run(_agent(_runtime_with(gate))._before_tool_call(invocation, None))

    assert result is invocation


def test_one_blocking_handler_beats_a_permissive_one() -> None:
    """A stale gate left over from a reload must not be able to override a deny."""
    hooks = Hooks()
    ExtensionRuntime(
        LoadExtensionsResult(
            extensions=[
                _extension({"tool_call": [_allow]}),
                _extension({"tool_call": [_deny]}),
            ],
            errors=[],
        ),
        hooks,
        _RuntimeRef(),
    )

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent)
    assert result.is_error is True


async def _allow(event, ctx):
    return None


async def _deny(event, ctx):
    return ToolCallEventResult(block=True, reason="nope")


def test_a_raising_handler_does_not_silently_allow_the_others_block() -> None:
    hooks = Hooks()
    ExtensionRuntime(
        LoadExtensionsResult(
            extensions=[
                _extension({"tool_call": [_explode]}),
                _extension({"tool_call": [_deny]}),
            ],
            errors=[],
        ),
        hooks,
        _RuntimeRef(),
    )

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent), "one broken extension hid another's block"


async def _explode(event, ctx):
    raise RuntimeError("bad extension")


# ── Context construction ─────────────────────────────────────────────────────
#
# ExtensionContext.from_runtime used to be called *outside* the guarded block,
# so a runtime that could not produce a context threw out of the registered
# hook handler. Hooks.emit caught it, logged, and returned no result — which
# for tool_call is read as "no objection". Nothing was recorded either, so it
# was silent as well as permissive: the worst pair, on the only hook that can
# stop a tool.


class _BadRuntime:
    """A runtime ExtensionContext.from_runtime cannot consume."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"runtime is unusable: {name}")


class _BadRuntimeRef:
    runtime = _BadRuntime()
    services: dict[str, Any] = {}
    service_owners: dict[str, Any] = {}


def _runtime_with_bad_context(handler) -> tuple[Hooks, ExtensionRuntime]:
    hooks = Hooks()
    ext_runtime = ExtensionRuntime(
        LoadExtensionsResult(extensions=[_extension({"tool_call": [handler]})], errors=[]),
        hooks,
        _BadRuntimeRef(),  # type: ignore[arg-type]
    )
    return hooks, ext_runtime


def test_a_context_failure_still_reaches_the_handler() -> None:
    """The handler gets ctx=None and can still object."""
    seen: list[Any] = []

    async def gate(event, ctx):
        seen.append(ctx)
        return ToolCallEventResult(block=True, reason="denied without a ctx")

    hooks, _ = _runtime_with_bad_context(gate)

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert seen == [None], "the handler never ran"
    assert isinstance(result, ToolResultContent)
    assert result.content == "denied without a ctx"


def test_a_context_failure_is_recorded_not_swallowed() -> None:
    async def gate(event, ctx):
        return None

    hooks, ext_runtime = _runtime_with_bad_context(gate)
    asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert ext_runtime.errors, "a context failure left no trace for the user to see"
    assert "context" in ext_runtime.errors[0].error
    assert ext_runtime.errors[0].event == "tool_call"


def test_reporting_an_error_never_raises() -> None:
    """_record_error runs inside an except block; raising there re-hides the failure.

    ``_BadRuntime`` raises on *every* attribute, including the
    ``report_extension_error`` lookup itself.
    """

    async def gate(event, ctx):
        return ToolCallEventResult(block=True, reason="still denied")

    hooks, ext_runtime = _runtime_with_bad_context(gate)

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent), "a failed report undid the block"
    assert ext_runtime.errors, "the record is kept even when nobody can be told"


def test_a_handler_crashing_on_a_none_context_is_recorded() -> None:
    """The realistic follow-on: the gate dereferences ctx.ui and dies."""

    async def gate(event, ctx):
        return ToolCallEventResult(block=True, reason=ctx.ui)  # AttributeError

    hooks, ext_runtime = _runtime_with_bad_context(gate)

    asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    events = [e.event for e in ext_runtime.errors]
    assert events.count("tool_call") >= 2, "context failure and handler failure both recorded"


@pytest.mark.parametrize("event_type", sorted(_INTERCEPTABLE_EVENTS))
def test_every_interceptable_event_propagates_its_result(event_type: str) -> None:
    """The split is the bug surface — pin it for all of them, not just tool_call."""
    sentinel = object()

    async def handler(event, ctx):
        return sentinel

    hooks = _runtime_with(handler, event_type)

    class _Event:
        type = event_type

    results = asyncio.run(hooks.emit(_Event()))  # type: ignore[arg-type]

    assert sentinel in results
