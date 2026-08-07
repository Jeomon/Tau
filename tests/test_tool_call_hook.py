"""Tests for the ``tool_call`` hook — the pre-execution interception point.

``ToolCallEvent`` was declared and exported long before it had an emit site, so
these tests pin both halves of the contract: that ``Agent._before_tool_call``
emits it and honours ``ToolCallEventResult``, and that the engine actually
cancels execution when the callback hands back a ``ToolResultContent``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tau.agent.service import Agent
from tau.engine.service import Engine
from tau.engine.types import EngineOptions
from tau.hooks.engine import ToolCallEvent, ToolCallEventResult
from tau.hooks.service import Hooks
from tau.message.types import ToolCallContent, ToolResultContent
from tau.tool.types import ToolInvocation, ToolResult


def _agent(hooks: Hooks) -> Any:
    """An Agent shell carrying only what ``_before_tool_call`` touches."""
    agent = Agent.__new__(Agent)
    agent.hooks = hooks  # type: ignore[attr-defined]
    return agent


def _invocation(params: dict | None = None) -> ToolInvocation:
    return ToolInvocation(
        id="call-1",
        name="terminal",
        cwd=Path("."),
        params=params if params is not None else {"cmd": "ls"},
    )


# ── Emission ─────────────────────────────────────────────────────────────────


def test_before_tool_call_emits_tool_call_event() -> None:
    hooks = Hooks()
    seen: list[ToolCallEvent] = []

    @hooks.on("tool_call")
    async def record(event, ctx=None):
        seen.append(event)

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert len(seen) == 1
    assert seen[0].tool_name == "terminal"
    assert seen[0].tool_call_id == "call-1"
    assert seen[0].input == {"cmd": "ls"}
    # No interceptable result — the invocation passes through untouched.
    assert isinstance(result, ToolInvocation)


def test_no_handlers_returns_the_invocation_unchanged() -> None:
    invocation = _invocation()

    result = asyncio.run(_agent(Hooks())._before_tool_call(invocation, None))

    assert result is invocation


# ── Blocking ─────────────────────────────────────────────────────────────────


def test_block_cancels_the_call_with_the_handler_reason() -> None:
    hooks = Hooks()

    @hooks.on("tool_call")
    async def deny(event, ctx=None):
        return ToolCallEventResult(block=True, reason="Denied by policy: rm -rf")

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent)
    assert result.is_error is True
    assert result.content == "Denied by policy: rm -rf"
    assert result.id == "call-1"
    assert result.metadata["blocked"] is True
    assert result.metadata["blocked_by"] == "extension"


def test_block_without_a_reason_names_the_tool() -> None:
    hooks = Hooks()

    @hooks.on("tool_call")
    async def deny(event, ctx=None):
        return ToolCallEventResult(block=True)

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent)
    assert "terminal" in result.content


def test_first_blocking_handler_wins_and_later_ones_do_not_run() -> None:
    hooks = Hooks()
    ran: list[str] = []

    @hooks.on("tool_call")
    async def first(event, ctx=None):
        ran.append("first")
        return ToolCallEventResult(block=True, reason="first")

    @hooks.on("tool_call")
    async def second(event, ctx=None):
        ran.append("second")
        return ToolCallEventResult(block=True, reason="second")

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent)
    assert result.content == "first"
    # Both handlers still *run* (emit fans out); only the first decision is read.
    assert ran == ["first", "second"]


# ── Rewriting ────────────────────────────────────────────────────────────────


def test_params_are_rewritten_on_the_invocation() -> None:
    hooks = Hooks()

    @hooks.on("tool_call")
    async def narrow(event, ctx=None):
        return ToolCallEventResult(params={"cmd": "ls -la ./src"})

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolInvocation)
    assert result.params == {"cmd": "ls -la ./src"}


def test_block_wins_over_params_on_the_same_result() -> None:
    hooks = Hooks()

    @hooks.on("tool_call")
    async def confused(event, ctx=None):
        return ToolCallEventResult(block=True, reason="no", params={"cmd": "rm -rf /"})

    result = asyncio.run(_agent(hooks)._before_tool_call(_invocation(), None))

    assert isinstance(result, ToolResultContent)
    assert result.content == "no"


def test_non_result_return_values_are_ignored() -> None:
    hooks = Hooks()

    @hooks.on("tool_call")
    async def chatty(event, ctx=None):
        return "not a ToolCallEventResult"

    invocation = _invocation()
    result = asyncio.run(_agent(hooks)._before_tool_call(invocation, None))

    assert result is invocation


# ── Engine integration ───────────────────────────────────────────────────────


class _RecordingTool:
    """Minimal tool that records whether it was ever executed."""

    name = "terminal"
    kind = "execute"
    prepare_arguments = None
    execution_mode = None

    def __init__(self) -> None:
        self.executed = False

    def validate(self, params: dict) -> tuple[bool, list[str]]:
        return True, []

    async def execute(
        self,
        invocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ):
        self.executed = True
        return ToolResult.ok(invocation.id, "ran")


@pytest.fixture
def engine_and_tool():
    tool = _RecordingTool()

    def _build(before_tool_call) -> Any:
        llm = SimpleNamespace(model=SimpleNamespace(name="test"))
        engine = Engine(
            cwd=Path("."),
            llm=llm,  # type: ignore[arg-type]
            tools=[],
            options=EngineOptions(before_tool_call=before_tool_call),
        )
        engine._tools = {"terminal": tool}  # type: ignore[dict-item]
        return engine

    return _build, tool


def test_engine_cancels_execution_when_the_gate_blocks(engine_and_tool) -> None:
    build, tool = engine_and_tool
    blocked = ToolResultContent(
        id="call-1", is_error=True, content="Blocked by policy", metadata={}
    )

    async def gate(invocation, signal):
        return blocked

    engine = build(gate)
    call = ToolCallContent(id="call-1", name="terminal", args={"cmd": "rm -rf /"})

    async def emit(event) -> None:
        return None

    result = asyncio.run(engine._execute(call, emit, None))

    assert result is blocked
    assert tool.executed is False, "the tool ran despite the gate blocking it"


def test_engine_runs_the_tool_when_the_gate_allows(engine_and_tool) -> None:
    build, tool = engine_and_tool

    async def gate(invocation, signal):
        return invocation

    engine = build(gate)
    call = ToolCallContent(id="call-1", name="terminal", args={"cmd": "ls"})

    async def emit(event) -> None:
        return None

    result = asyncio.run(engine._execute(call, emit, None))

    assert tool.executed is True
    assert result.is_error is False
