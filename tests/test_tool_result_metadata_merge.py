"""Two extensions annotating the same tool result must both be heard.

The engine merged `ToolResultEventResult.metadata` with `{**old, **new}` and
then `break`ed after the first result, so the merge could never span handlers:
whichever extension loaded first silently erased every other extension's keys.
With lsp and permissions both installed, the permission record vanished from
exactly the calls lsp had something to say about — files with diagnostics.

Content override stays first-wins. Two handlers rewriting the same output is a
genuine conflict whose winner would otherwise depend on load order. Metadata is
annotation, not rewriting, and several annotators is the ordinary case.

These drive `Engine._execute`, the real path, rather than a copy of its loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tau.engine.service import Engine
from tau.hooks.engine import ToolResultEventResult
from tau.message.types import ToolCallContent
from tau.tool.types import ToolResult


class _Echo:
    """Minimal real tool: enough surface for Engine._execute to run it.

    Duck-typed rather than a `Tool` subclass, matching `_EchoTool` in
    test_engine_steering.py — the ABC's constructor wants a schema and kind
    that contribute nothing here.
    """

    name = "echo"
    kind = None
    execution_mode = None
    prepare_arguments = None

    def validate(self, params: Any) -> tuple[bool, list]:
        return True, []

    async def execute(
        self,
        invocation: Any,
        tool_execution_update_callback: Any = None,
        signal: Any = None,
        context: Any = None,
    ) -> ToolResult:
        return ToolResult(id=invocation.id, content="original")


async def _run(handlers: list) -> Any:
    engine = Engine(cwd=Path("."), llm=None, tools=[_Echo()], system_prompt="")  # type: ignore[arg-type]
    for handler in handlers:
        engine.hooks.on("tool_result")(handler)

    async def _emit(_event: Any) -> None:
        return None

    return await engine._execute(ToolCallContent(id="tc1", name="echo", args={}), _emit, None)


@pytest.mark.asyncio
async def test_metadata_from_every_handler_survives() -> None:
    async def lsp(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(metadata={"_extra_blocks": ["lsp"]})

    async def permissions(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(metadata={"_permission": {"state": "allow"}})

    result = await _run([lsp, permissions])

    assert sorted(result.metadata) == ["_extra_blocks", "_permission"]


@pytest.mark.asyncio
async def test_a_later_handler_cannot_steal_the_content() -> None:
    async def first(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(content="first")

    async def second(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(content="second")

    result = await _run([first, second])

    assert result.content == "first"


@pytest.mark.asyncio
async def test_a_metadata_only_handler_does_not_claim_the_content_slot() -> None:
    """It used to: the first result broke the loop whatever it carried."""

    async def annotate(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(metadata={"_permission": {}})

    async def override(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(content="the real override")

    result = await _run([annotate, override])

    assert result.content == "the real override"
    assert "_permission" in result.metadata


@pytest.mark.asyncio
async def test_any_handler_can_terminate() -> None:
    async def annotate(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(metadata={"a": 1})

    async def stop(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(terminate=True)

    result = await _run([annotate, stop])

    assert result.terminate is True


@pytest.mark.asyncio
async def test_handlers_returning_nothing_are_ignored() -> None:
    async def quiet(_event: Any) -> None:
        return None

    async def annotate(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(metadata={"_permission": {}})

    result = await _run([quiet, annotate, quiet])

    assert "_permission" in result.metadata


@pytest.mark.asyncio
async def test_an_is_error_override_still_applies() -> None:
    async def fail(_event: Any) -> ToolResultEventResult:
        return ToolResultEventResult(is_error=True)

    result = await _run([fail])

    assert result.is_error is True


@pytest.mark.asyncio
async def test_a_blocked_call_carries_the_blockers_metadata() -> None:
    """A block skips execution, so `tool_result` never fires for it.

    Without `ToolCallEventResult.metadata` the only structured trace of a
    denial was the host's own `blocked` flag, with the actual reason surviving
    as prose inside `content`.
    """
    from tau.agent.service import Agent
    from tau.hooks.engine import ToolCallEventResult

    agent = Agent.__new__(Agent)

    class _Hooks:
        async def emit(self, _event: Any) -> list:
            return [
                ToolCallEventResult(
                    block=True,
                    reason="Denied by policy.",
                    metadata={"_permission": {"state": "deny", "pattern": "**/.env"}},
                )
            ]

    agent.hooks = _Hooks()  # type: ignore[assignment]

    class _Invocation:
        id = "tc1"
        name = "read"
        params: dict = {}

    result = await agent._before_tool_call(_Invocation(), None)  # type: ignore[arg-type]

    assert result.metadata["blocked"] is True
    assert result.metadata["blocked_by"] == "extension"
    assert result.metadata["_permission"]["pattern"] == "**/.env"
    assert result.is_error is True


@pytest.mark.asyncio
async def test_a_blocker_without_metadata_still_works() -> None:
    from tau.agent.service import Agent
    from tau.hooks.engine import ToolCallEventResult

    agent = Agent.__new__(Agent)

    class _Hooks:
        async def emit(self, _event: Any) -> list:
            return [ToolCallEventResult(block=True, reason="no")]

    agent.hooks = _Hooks()  # type: ignore[assignment]

    class _Invocation:
        id = "tc1"
        name = "read"
        params: dict = {}

    result = await agent._before_tool_call(_Invocation(), None)  # type: ignore[arg-type]

    assert result.metadata == {"blocked": True, "blocked_by": "extension"}


@pytest.mark.asyncio
async def test_a_blocker_can_attribute_itself() -> None:
    """`blocked_by` is a value, so a new blocker is not a new key.

    Naming the mechanism in the key — as `blocked_by_extension` did — meant
    every future source of a block would need its own key, and every consumer
    would need to learn it just to answer "was this call stopped".
    """
    from tau.agent.service import Agent
    from tau.hooks.engine import ToolCallEventResult

    agent = Agent.__new__(Agent)

    class _Hooks:
        async def emit(self, _event: Any) -> list:
            return [
                ToolCallEventResult(block=True, reason="no", metadata={"blocked_by": "sandbox"})
            ]

    agent.hooks = _Hooks()  # type: ignore[assignment]

    class _Invocation:
        id = "tc1"
        name = "read"
        params: dict = {}

    result = await agent._before_tool_call(_Invocation(), None)  # type: ignore[arg-type]

    assert result.metadata["blocked"] is True
    assert result.metadata["blocked_by"] == "sandbox"
