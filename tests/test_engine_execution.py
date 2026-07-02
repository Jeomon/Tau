from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from tau.engine.service import Engine
from tau.engine.types import EngineOptions
from tau.message.types import ToolCallContent
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
