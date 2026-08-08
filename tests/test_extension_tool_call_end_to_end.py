"""The whole `tool_call` chain in one test: extension file → loader → engine.

The two legs of this were already covered separately — `ExtensionRuntime`
bridging a handler onto the hooks bus (test_extension_tool_call_block.py) and
the engine turning a returned `ToolResultContent` into the tool's result
(test_engine_execution.py) — but nothing joined them. That gap is how the
original fail-open survived a passing suite: the tests registered with
`@hooks.on("tool_call")`, straight onto the bus, which no extension can do,
so the contract looked verified while the path every extension actually takes
dropped the result on the floor.

So this deliberately starts from a real file on disk and asserts on the one
thing that matters to a permission gate: the tool did not run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from tau.agent.service import Agent
from tau.engine.service import Engine
from tau.engine.types import EngineContext, EngineOptions
from tau.extensions.loader import ExtensionLoader
from tau.extensions.runtime import ExtensionRuntime
from tau.hooks.service import Hooks
from tau.inference.types import EndEvent, StopReason, ToolCallEndEvent
from tau.message.types import ToolCallContent, UserMessage
from tau.settings.types import ExtensionEntry
from tau.tool.types import Tool, ToolKind, ToolResult

GATE = """
from tau.hooks import ToolCallEventResult


def register(tau):
    @tau.on("tool_call")
    async def gate(event, ctx):
        if event.tool_name == "danger":
            return ToolCallEventResult(
                block=True,
                reason="Denied by policy.",
                metadata={"policy": "no-danger"},
            )
        return None
"""

ALLOW = """
def register(tau):
    @tau.on("tool_call")
    async def gate(event, ctx):
        return None
"""

REWRITE = """
from tau.hooks import ToolCallEventResult


def register(tau):
    @tau.on("tool_call")
    async def gate(event, ctx):
        return ToolCallEventResult(params={"target": "safe"})
"""


class _RuntimeRef:
    runtime = None
    services: dict[str, Any] = {}
    service_owners: dict[str, Any] = {}


class _Params(BaseModel):
    target: str = ""


class _RecordingTool(Tool):
    """Records whether it ran, and with what — the only assertion that matters."""

    def __init__(self, name: str = "danger") -> None:
        super().__init__(
            name=name,
            description="Test tool.",
            schema=_Params,
            kind=ToolKind.Execute,
        )
        self.calls: list[dict] = []

    async def execute(
        self,
        invocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ) -> ToolResult:
        self.calls.append(dict(invocation.params))
        return ToolResult.ok(invocation.id, "the tool ran")


class _Model:
    name = "fake-model"


class _ScriptedLLM:
    """Emits one tool call, then stops."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self.model = _Model()
        self.api = SimpleNamespace(options=SimpleNamespace(headers={}))
        self.provider_id = "fake"
        self._turns = [
            [
                ToolCallEndEvent(
                    tool_call=ToolCallContent(id="tc1", name=tool_name, args={"target": "prod"})
                ),
                EndEvent(reason=StopReason.ToolCalls),
            ]
        ]

    def stream(self, ctx):
        return self._gen(ctx)

    async def _gen(self, ctx):
        turn = self._turns.pop(0) if self._turns else [EndEvent(reason=StopReason.Stop)]
        for event in turn:
            yield event


async def _run_turn(tmp_path: Path, source: str, *, tool_name: str = "danger"):
    """Load a real extension file, wire it the way tau does, drive one turn."""
    extension = tmp_path / "gate_ext.py"
    extension.write_text(source)

    loader = ExtensionLoader(
        extra_entries=[ExtensionEntry(path=str(extension))],
        llm=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cwd=tmp_path,
        runtime_ref=_RuntimeRef(),
    )
    loaded = await loader.load()
    assert not loaded.errors, f"extension failed to load: {loaded.errors}"
    assert loaded.extensions, "no extension loaded"

    hooks = Hooks()
    ExtensionRuntime(loaded, hooks, _RuntimeRef())

    # Only `hooks` is read by the gate; the rest of Agent is irrelevant here.
    agent = Agent.__new__(Agent)
    agent.hooks = hooks  # type: ignore[attr-defined]

    tool = _RecordingTool(tool_name)
    engine = Engine(
        cwd=tmp_path,
        llm=_ScriptedLLM(tool_name),  # type: ignore[arg-type]
        tools=[tool],
        system_prompt="",
    )
    engine.options = EngineOptions(before_tool_call=agent._before_tool_call)

    results: list = []
    engine.hooks.subscribe(
        lambda e: results.append(e) if getattr(e, "type", "") == "tool_execution_end" else None
    )
    # `run()` rebuilds the tool table from the context (engine/service.py), so
    # the constructor's list alone leaves the call unresolvable.
    await engine.run(
        EngineContext(system_prompt="", messages=[UserMessage.from_text("go")], tools=[tool])
    )
    return tool, results


@pytest.mark.asyncio
async def test_an_extension_file_blocks_a_tool_before_it_runs(tmp_path: Path) -> None:
    tool, results = await _run_turn(tmp_path, GATE)

    assert tool.calls == [], "the tool ran despite the extension blocking it"
    assert results, "no tool result was produced"
    result = results[-1].tool_result
    assert result.is_error is True
    assert result.content == "Denied by policy."


@pytest.mark.asyncio
async def test_the_block_carries_structured_metadata(tmp_path: Path) -> None:
    """A blocked call never reaches `tool_result`, so this is the only place a
    denial can say why in fields rather than in prose."""
    _tool, results = await _run_turn(tmp_path, GATE)

    metadata = results[-1].tool_result.metadata
    assert metadata["blocked"] is True
    assert metadata["blocked_by"] == "extension"
    assert metadata["policy"] == "no-danger", "the handler's own keys must survive"


@pytest.mark.asyncio
async def test_a_handler_that_allows_lets_the_tool_run(tmp_path: Path) -> None:
    """Control: without this, a chain broken anywhere would still 'pass' the
    blocking tests by never running tools at all."""
    tool, results = await _run_turn(tmp_path, ALLOW)

    assert tool.calls == [{"target": "prod"}]
    assert results[-1].tool_result.is_error is False


@pytest.mark.asyncio
async def test_a_handler_can_rewrite_params_instead_of_refusing(tmp_path: Path) -> None:
    tool, _results = await _run_turn(tmp_path, REWRITE)

    assert tool.calls == [{"target": "safe"}], "the rewritten params did not reach the tool"


@pytest.mark.asyncio
async def test_an_unguarded_tool_is_unaffected(tmp_path: Path) -> None:
    """The gate names one tool; everything else runs as before."""
    tool, _results = await _run_turn(tmp_path, GATE, tool_name="harmless")

    assert tool.calls == [{"target": "prod"}]


def test_module_imports_without_a_running_loop() -> None:
    """Guards the harness itself: `_run_turn` must not need an ambient loop to
    be constructed, or a failure here would look like a hook failure."""
    assert asyncio.iscoroutinefunction(_run_turn)
