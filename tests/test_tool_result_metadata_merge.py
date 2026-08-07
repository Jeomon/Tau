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
