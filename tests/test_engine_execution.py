from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tau.engine.service import Engine
from tau.engine.types import EngineOptions
from tau.message.types import ToolCallContent, UserMessage
from tau.tool.types import ToolExecutionMode


def _engine(options: EngineOptions | None = None) -> Any:
    llm = SimpleNamespace(model=SimpleNamespace(name="test"))
    return Engine(cwd=Path("."), llm=llm, tools=[], options=options)  # type: ignore[arg-type]


def test_public_engine_api_and_compatibility_aliases() -> None:
    from tau.agent.types import AgentContext
    from tau.engine import (
        Agent,
        AgentOptions,
        AgentState,
        EngineContext,
        EngineOptions,
        EngineState,
    )

    assert Agent is Engine
    assert AgentState is EngineState
    assert AgentOptions is EngineOptions
    assert AgentContext is EngineContext


def test_default_execution_runs_all_parallel_tools_concurrently() -> None:
    engine = _engine()
    calls = [
        ToolCallContent(id="one", name="read_one", args={}),
        ToolCallContent(id="two", name="read_two", args={}),
    ]
    engine._tools = {
        call.name: SimpleNamespace(execution_mode=ToolExecutionMode.Parallel) for call in calls
    }
    expected = [SimpleNamespace(id="one"), SimpleNamespace(id="two")]
    engine._parallel_execute = AsyncMock(return_value=expected)
    engine._sequential_execute = AsyncMock()

    result = asyncio.run(engine._execute_tool_calls(calls, AsyncMock()))

    assert result == expected
    engine._parallel_execute.assert_awaited_once()
    engine._sequential_execute.assert_not_awaited()


def test_sequential_tool_serializes_the_whole_batch_in_source_order() -> None:
    engine = _engine()
    calls = [
        ToolCallContent(id="one", name="read", args={}),
        ToolCallContent(id="two", name="write", args={}),
        ToolCallContent(id="three", name="read_again", args={}),
    ]
    engine._tools = {
        "read": SimpleNamespace(execution_mode=ToolExecutionMode.Parallel),
        "write": SimpleNamespace(execution_mode=ToolExecutionMode.Sequential),
        "read_again": SimpleNamespace(execution_mode=ToolExecutionMode.Parallel),
    }
    expected = [SimpleNamespace(id=call.id) for call in calls]
    engine._parallel_execute = AsyncMock()
    engine._sequential_execute = AsyncMock(return_value=expected)

    result = asyncio.run(engine._execute_tool_calls(calls, AsyncMock()))

    assert result == expected
    assert engine._sequential_execute.await_args is not None
    executed_calls = engine._sequential_execute.await_args.args[0]
    assert [call.id for call in executed_calls] == ["one", "two", "three"]
    engine._parallel_execute.assert_not_awaited()


def test_explicit_parallel_mode_does_not_override_tool_safety() -> None:
    engine = _engine()
    engine.options = EngineOptions(execution_mode=ToolExecutionMode.Parallel)
    calls = [ToolCallContent(id="one", name="write", args={})]
    engine._tools = {
        "write": SimpleNamespace(execution_mode=ToolExecutionMode.Sequential),
    }
    engine._parallel_execute = AsyncMock()
    engine._sequential_execute = AsyncMock(return_value=[])

    asyncio.run(engine._execute_tool_calls(calls, AsyncMock()))

    engine._sequential_execute.assert_awaited_once()
    engine._parallel_execute.assert_not_awaited()


def test_non_cooperative_tool_is_timed_out_by_engine() -> None:
    engine = _engine(EngineOptions(tool_timeout_seconds=0.02))

    async def never_finishes():
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def run() -> None:
        result, aborted, timed_out = await engine._run_tool_with_controls(never_finishes(), None)
        assert result is None
        assert aborted is False
        assert timed_out is True

    asyncio.run(run())


def test_engine_abort_cancels_a_non_cooperative_tool() -> None:
    engine = _engine(EngineOptions(tool_timeout_seconds=None))
    signal = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_finishes():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def run() -> None:
        task = asyncio.create_task(engine._run_tool_with_controls(never_finishes(), signal))
        await asyncio.sleep(0)
        signal.set()
        result, aborted, timed_out = await asyncio.wait_for(task, timeout=1)
        assert result is None
        assert aborted is True
        assert timed_out is False
        assert cancelled.is_set()

    asyncio.run(run())


def test_parallel_tool_execution_is_bounded() -> None:
    engine = _engine(EngineOptions(max_parallel_tool_calls=2))
    calls = [ToolCallContent(id=str(index), name="test", args={}) for index in range(5)]
    active = 0
    maximum_active = 0

    async def execute(call, _emit, _signal):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(id=call.id)

    engine._execute = execute
    result = asyncio.run(engine._parallel_execute(calls, AsyncMock(), None))

    assert maximum_active == 2
    assert [item.id for item in result] == [call.id for call in calls]


def test_parallel_tool_execution_creates_only_bounded_workers() -> None:
    limit = 2
    engine = _engine(EngineOptions(max_parallel_tool_calls=limit))
    calls = [ToolCallContent(id=str(index), name="test", args={}) for index in range(50)]

    async def execute(call, _emit, _signal):
        return SimpleNamespace(id=call.id)

    async def run() -> list[Any]:
        created = []
        real_create_task = asyncio.create_task

        def count_create_task(coro, *args, **kwargs):
            created.append(coro)
            return real_create_task(coro, *args, **kwargs)

        engine._execute = execute
        with patch("tau.engine.service.asyncio.create_task", side_effect=count_create_task):
            result = await engine._parallel_execute(calls, AsyncMock(), None)
        assert len(created) == limit
        return result

    result = asyncio.run(run())

    assert [item.id for item in result] == [call.id for call in calls]


def test_continue_preserves_the_supplied_abort_signal() -> None:
    engine = _engine()
    supplied_signal = asyncio.Event()
    received_signals = []
    engine.state.messages = [UserMessage.from_text("continue")]

    async def loop(_messages, _emit, signal):
        received_signals.append(signal)

    engine._loop = loop
    asyncio.run(engine.run_continue(signal=supplied_signal))

    assert received_signals == [supplied_signal]
