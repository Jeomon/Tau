"""Engine-loop tests for two bugs fixed in tau/engine/service.py:

1. Length-truncated tool calls — a message that hit StopReason.Length may carry
   tool calls whose arguments look valid but are silently cut off mid-generation.
   Those must be failed with an error result (asking the model to reissue the
   call) instead of executed as if complete.

2. Live tool-set refresh — an extension changing ``engine.tools`` mid-run (e.g.
   via a tool calling ``set_active_tools``) must be reflected in the very next
   provider request within the same run, not only on the next ``run()`` call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from tau.engine.service import Engine
from tau.engine.types import EngineContext
from tau.inference.types import EndEvent, StopReason, TextEndEvent, ToolCallEndEvent
from tau.message.types import TextContent, ToolCallContent, UserMessage


def run(coro):
    return asyncio.run(coro)


class _Model:
    name = "fake-model"


class _Options:
    headers: dict = {}


class _Api:
    options = _Options()


class ScriptedLLM:
    """Replays scripted turns and records the tools each provider request saw."""

    def __init__(self, turns: list[list]):
        self._turns = list(turns)
        self.calls = 0
        self.tools_seen: list[list] = []
        self.model = _Model()
        self.api = _Api()
        self.provider_id = "fake-provider"

    def stream(self, ctx):
        return self._gen(ctx)

    async def _gen(self, ctx):
        self.calls += 1
        self.tools_seen.append(list(ctx.tools))
        turn = self._turns.pop(0) if self._turns else [EndEvent(reason=StopReason.Stop)]
        for ev in turn:
            yield ev


def _text_turn(text: str = "ok") -> list:
    return [TextEndEvent(text=TextContent(content=text)), EndEvent(reason=StopReason.Stop)]


def _tool_turn(call_id: str = "tc1", name: str = "some_tool") -> list:
    return [
        ToolCallEndEvent(tool_call=ToolCallContent(id=call_id, name=name, args={"x": "y"})),
        EndEvent(reason=StopReason.Length),
    ]


def test_length_truncated_tool_calls_are_failed_not_executed() -> None:
    llm = ScriptedLLM([_tool_turn(), _text_turn()])
    engine = Engine(cwd=Path("."), llm=llm, tools=[], system_prompt="")  # type: ignore[arg-type]
    executed: list[str] = []

    async def _fail_if_called(*_args, **_kwargs):
        executed.append("called")
        raise AssertionError("tool calls from a length-truncated message must not execute")

    engine._execute_tool_calls = _fail_if_called  # type: ignore[assignment]

    run(engine.run(EngineContext(system_prompt="", messages=[UserMessage.from_text("hi")])))

    assert executed == []
    assert llm.calls == 2
    # Turn 2 must have seen a tool-result message telling it to reissue the call.
    from tau.message.types import ToolMessage, ToolResultContent

    history = engine.state.messages
    tool_msgs = [m for m in history if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    results = [c for c in tool_msgs[0].contents if isinstance(c, ToolResultContent)]
    assert len(results) == 1
    assert results[0].is_error is True
    assert results[0].id == "tc1"
    assert "reissue" in results[0].content.lower()


def test_extension_tool_change_applies_to_next_request_in_same_run() -> None:
    llm = ScriptedLLM([_tool_turn(name="lookup"), _text_turn()])
    # Length above triggers the failed-not-executed path, which never calls
    # _execute_tool_calls — swap to StopReason.ToolCalls so the real execution
    # path (and thus the mid-turn mutation point) actually runs.
    llm._turns[0][-1] = EndEvent(reason=StopReason.ToolCalls)

    initial_tools: list = [SimpleNamespace(name="old_tool")]
    new_tools: list = [SimpleNamespace(name="new_tool")]
    engine = Engine(cwd=Path("."), llm=llm, tools=initial_tools, system_prompt="")  # type: ignore[arg-type]

    original_execute = engine._execute_tool_calls

    async def wrapped(*args, **kwargs):
        result = await original_execute(*args, **kwargs)
        # Simulate an extension tool calling pi-style set_active_tools() mid-turn.
        engine.tools = new_tools  # type: ignore[assignment]
        return result

    engine._execute_tool_calls = wrapped  # type: ignore[assignment]

    run(
        engine.run(
            EngineContext(
                system_prompt="", messages=[UserMessage.from_text("hi")], tools=initial_tools
            )
        )
    )

    assert llm.calls == 2
    assert llm.tools_seen[0] == initial_tools
    assert llm.tools_seen[1] == new_tools
