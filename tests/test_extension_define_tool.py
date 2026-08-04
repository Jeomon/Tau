"""Tests for ``define_tool`` — the one-call custom tool helper.

``register_tool`` already accepted any ``Tool``; what this adds is authoring one
without hand-writing a Pydantic model and a ``Tool`` subclass. The thing worth
testing is therefore that the shortcut produces a *genuine* ``Tool``: same
schema validation, same policy fields, same ``ToolResult`` contract as one
written out by hand.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel, Field

from tau.extensions import define_tool
from tau.tool.types import (
    Tool,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


def _call(tool: Tool, **params) -> ToolResult:
    invocation = ToolInvocation(id="call-1", name=tool.name, cwd=None, params=params)
    return asyncio.run(tool.execute(invocation))


def test_produces_a_real_tool() -> None:
    tool = define_tool("noop", "does nothing", execute=lambda p: "ok")

    assert isinstance(tool, Tool)
    assert tool.name == "noop"
    assert tool.description == "does nothing"
    # Least-privileged by default: a tool only gets a broader policy on request.
    assert tool.kind is ToolKind.Read
    assert tool.execution_mode is ToolExecutionMode.Sequential


def test_dict_parameters_become_a_validating_schema() -> None:
    tool = define_tool(
        "current_time",
        "Get the current time in any IANA timezone",
        parameters={"timezone": (str, "e.g. Europe/Vienna")},
        execute=lambda p: p["timezone"],
    )

    schema = tool.schema.model_json_schema()
    assert schema["required"] == ["timezone"]
    # The description is what the model reads to fill the argument in.
    assert schema["properties"]["timezone"]["description"] == "e.g. Europe/Vienna"

    ok, _ = tool.validate({"timezone": "Europe/Vienna"})
    missing, errors = tool.validate({})
    assert ok and not missing
    assert any("timezone" in e for e in errors)


def test_declared_default_is_actually_applied() -> None:
    """A default is inert unless the args are run through the schema first:
    the model just omits the argument and execute() sees no key at all."""
    tool = define_tool(
        "tz",
        "d",
        parameters={"timezone": (str, "zone", "UTC")},
        execute=lambda p: p["timezone"],
    )

    assert _call(tool).content == "UTC"
    assert _call(tool, timezone="Asia/Kolkata").content == "Asia/Kolkata"


def test_types_are_coerced_and_bad_arguments_reported_not_raised() -> None:
    tool = define_tool("n", "d", parameters={"n": int}, execute=lambda p: type(p["n"]).__name__)

    assert _call(tool, n="42").content == "int"

    result = _call(tool, n="not-a-number")
    assert result.is_error
    assert "Invalid arguments" in result.content


def test_basemodel_parameters_are_used_as_is() -> None:
    class Params(BaseModel):
        query: str = Field(description="what to look up")
        limit: int = 5

    tool = define_tool(
        "search", "d", parameters=Params, execute=lambda p: f"{p['query']}/{p['limit']}"
    )

    assert tool.schema is Params
    assert _call(tool, query="cats").content == "cats/5"


def test_no_parameters() -> None:
    tool = define_tool("ping", "d", execute=lambda p: "pong")

    assert _call(tool).content == "pong"
    assert tool.schema.model_json_schema().get("properties", {}) == {}


def test_async_execute_is_awaited() -> None:
    async def run(params):
        await asyncio.sleep(0)
        return "async result"

    assert _call(define_tool("a", "d", execute=run)).content == "async result"


def test_sync_execute_does_not_block_the_event_loop() -> None:
    """A synchronous tool doing real work must not stall every other task."""

    def slow(params):
        time.sleep(0.2)
        return "done"

    tool = define_tool("slow", "d", execute=slow)

    async def race() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.ensure_future(ticker())
        invocation = ToolInvocation(id="s", name="slow", cwd=None, params={})
        await tool.execute(invocation)
        task.cancel()
        return ticks

    assert asyncio.run(race()) > 0  # 0 would mean the loop was stalled


@pytest.mark.parametrize(
    ("returned", "expected_content", "expected_meta"),
    [
        ("plain string", "plain string", {}),
        (None, "", {}),
        (("body", {"k": 1}), "body", {"k": 1}),
        (42, "42", {}),
    ],
)
def test_return_shapes_are_coerced(returned, expected_content, expected_meta) -> None:
    tool = define_tool("r", "d", execute=lambda p: returned)
    result = _call(tool)

    assert result.content == expected_content
    assert result.metadata == expected_meta
    assert not result.is_error


def test_toolresult_passthrough_gets_the_invocation_id() -> None:
    tool = define_tool("tr", "d", execute=lambda p: ToolResult(id="", content="raw", is_error=True))
    result = _call(tool)

    assert result.content == "raw"
    assert result.is_error
    assert result.id == "call-1"  # authors don't know the id up front


def test_exceptions_become_error_results() -> None:
    def boom(params):
        raise ValueError("nope")

    result = _call(define_tool("b", "d", execute=boom))

    assert result.is_error
    assert "ValueError: nope" in result.content


def test_context_arguments_are_injected_by_name() -> None:
    def needs_context(params, invocation, signal):
        return f"{invocation.id}|{signal}"

    assert _call(define_tool("c", "d", execute=needs_context)).content == "call-1|None"


def test_plain_signature_is_left_alone() -> None:
    """Only the known context names are injected; an ordinary function stays ordinary."""

    def just_params(params):
        return str(sorted(params))

    tool = define_tool("p", "d", parameters={"a": int}, execute=just_params)
    assert _call(tool, a=1).content == "['a']"


def test_kind_and_execution_mode_are_overridable() -> None:
    tool = define_tool(
        "sh",
        "d",
        execute=lambda p: "",
        kind=ToolKind.Execute,
        execution_mode=ToolExecutionMode.Parallel,
    )

    assert tool.kind is ToolKind.Execute
    assert tool.execution_mode is ToolExecutionMode.Parallel


def test_tool_options_pass_through() -> None:
    tool = define_tool("x", "d", execute=lambda p: "", prompt_snippet="use sparingly")

    assert tool.prompt_snippet == "use sparingly"


def test_rejects_bad_arguments() -> None:
    with pytest.raises(TypeError):
        define_tool("x", "d", execute="not callable")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        define_tool("x", "d", execute=lambda p: "", parameters=["not", "a", "dict"])  # type: ignore[arg-type]
