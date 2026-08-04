"""``define_tool``: declare a custom tool in one call.

Registering a tool has always been possible with :meth:`ExtensionAPI.register_tool`,
but authoring one meant two pieces of ceremony: a Pydantic model for the schema
and a :class:`~tau.tool.types.Tool` subclass overriding an ``execute`` with four
parameters, most of which a simple tool ignores. For a tool that reads a clock
that is a lot of scaffolding around one line of real work.

``define_tool`` collapses both into a single call, and returns an ordinary
``Tool`` — so ``register_tool`` and everything downstream (schema validation,
policy, rendering) is unchanged::

    from tau.extensions import define_tool

    current_time = define_tool(
        name="current_time",
        description="Get the current time in any IANA timezone",
        parameters={"timezone": (str, "e.g. Europe/Vienna")},
        execute=lambda p: datetime.now(ZoneInfo(p["timezone"])).isoformat(),
    )

    def register(tau):
        tau.register_tool(current_time)
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, create_model

from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

# Names a user's execute() may declare to receive the richer call context. Anything
# outside this set is left alone, so a plain ``def run(params)`` stays plain.
_CONTEXT_ARGS = frozenset({"invocation", "context", "signal", "update", "tool_call_id"})

ParameterSpec = type[BaseModel] | dict[str, Any] | None


def _build_schema(name: str, parameters: ParameterSpec) -> type[BaseModel]:
    """Turn a parameter spec into the Pydantic model ``Tool`` expects.

    Accepts what an author is most likely to reach for:

    - ``None`` — the tool takes no arguments.
    - a ``BaseModel`` subclass — used as-is, for full control over validation.
    - ``{"timezone": str}`` — a required field of that type.
    - ``{"timezone": (str, "e.g. Europe/Vienna")}`` — with a description, which
      reaches the model and is usually what makes a parameter self-explanatory.
    - ``{"timezone": (str, "...", "UTC")}`` — description plus a default, which
      also makes the field optional.
    """
    if parameters is None:
        return create_model(f"{name.title().replace('_', '')}Params")
    if isinstance(parameters, type) and issubclass(parameters, BaseModel):
        return parameters
    if not isinstance(parameters, dict):
        raise TypeError(
            f"define_tool(parameters=...) must be a dict, a BaseModel subclass or None, "
            f"got {type(parameters).__name__}"
        )

    fields: dict[str, Any] = {}
    for field_name, spec in parameters.items():
        if isinstance(spec, tuple):
            if not spec:
                raise ValueError(f"parameter {field_name!r} has an empty spec tuple")
            annotation = spec[0]
            description = spec[1] if len(spec) > 1 else None
            # A third element is a default, which also makes the field optional.
            default = spec[2] if len(spec) > 2 else ...
        else:
            annotation, description, default = spec, None, ...
        fields[field_name] = (annotation, Field(default, description=description))

    return create_model(f"{name.title().replace('_', '')}Params", **fields)


def _coerce_result(returned: Any, invocation: ToolInvocation) -> ToolResult:
    """Let ``execute`` return whatever is natural and still hand back a ToolResult."""
    if isinstance(returned, ToolResult):
        # Authors rarely know the invocation id up front; fill it in if they left it blank.
        if not returned.id:
            returned.id = invocation.id
        return returned
    if returned is None:
        return ToolResult.ok(id=invocation.id, content="")
    if isinstance(returned, tuple) and len(returned) == 2 and isinstance(returned[1], dict):
        content, metadata = returned
        return ToolResult.ok(id=invocation.id, content=str(content), metadata=metadata)
    return ToolResult.ok(id=invocation.id, content=str(returned))


def define_tool(
    name: str,
    description: str,
    execute: Callable[..., Any],
    *,
    parameters: ParameterSpec = None,
    kind: ToolKind = ToolKind.Read,
    execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
    **tool_options: Any,
) -> Tool:
    """Build a ready-to-register :class:`Tool` from a function.

    Args:
        name: Tool name the model calls.
        description: What the tool does — this is what the model reads to decide
            whether to call it, so it is worth writing properly.
        execute: The work. Sync or async. Receives the validated parameters dict
            as its first argument; may additionally declare any of
            ``invocation``, ``context``, ``signal``, ``update`` or
            ``tool_call_id`` by name to receive them. Return a ``str``, a
            ``(str, metadata_dict)`` pair, a ``ToolResult``, or ``None``.
        parameters: See :func:`_build_schema`.
        kind: Execution-policy category (Read/Edit/Write/Execute/Web). Defaults
            to ``Read``, the least-privileged option, so a tool only gets a
            broader policy when its author asks for one.
        execution_mode: How the engine schedules concurrent calls.
        **tool_options: Passed straight through to ``Tool.__init__`` —
            ``render_call``, ``render_result``, ``prompt_snippet``, etc.

    Returns:
        A ``Tool`` instance, ready for ``tau.register_tool(...)``.
    """
    if not callable(execute):
        raise TypeError(f"define_tool(execute=...) must be callable, got {type(execute).__name__}")

    schema = _build_schema(name, parameters)
    is_async = inspect.iscoroutinefunction(execute)

    # Worked out once at definition time rather than on every call.
    try:
        declared = set(inspect.signature(execute).parameters)
    except (TypeError, ValueError):  # builtins and C functions have no signature
        declared = set()
    wanted = _CONTEXT_ARGS & declared

    class _DefinedTool(Tool):
        async def execute(  # type: ignore[override]
            self,
            invocation: ToolInvocation,
            tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
            signal: AbortSignal | None = None,
            context: ToolContext | None = None,
        ) -> ToolResult:
            extras: dict[str, Any] = {}
            if "invocation" in wanted:
                extras["invocation"] = invocation
            if "context" in wanted:
                extras["context"] = context
            if "signal" in wanted:
                extras["signal"] = signal
            if "update" in wanted:
                extras["update"] = tool_execution_update_callback
            if "tool_call_id" in wanted:
                extras["tool_call_id"] = invocation.id

            # Run the raw arguments through the schema so declared defaults are
            # filled in and types are coerced. Without this a default is inert:
            # the model simply omits the argument and execute() sees no key at
            # all. Validation failures are reported rather than raised, matching
            # how execution errors below are handled.
            try:
                params = self.schema(**invocation.params).model_dump()
            except Exception as exc:
                return ToolResult(
                    id=invocation.id,
                    content=f"Invalid arguments for {self.name}: {exc}",
                    is_error=True,
                )

            try:
                if is_async:
                    returned = await execute(params, **extras)
                else:
                    # Kept off the event loop: a synchronous tool doing real work
                    # would otherwise stall every other task in the process.
                    loop = asyncio.get_running_loop()
                    returned = await loop.run_in_executor(None, lambda: execute(params, **extras))
            except Exception as exc:
                return ToolResult(
                    id=invocation.id,
                    content=f"{type(exc).__name__}: {exc}",
                    is_error=True,
                )
            return _coerce_result(returned, invocation)

    _DefinedTool.__name__ = f"{name.title().replace('_', '')}Tool"
    _DefinedTool.__qualname__ = _DefinedTool.__name__

    return _DefinedTool(
        name=name,
        description=description,
        schema=schema,
        kind=kind,
        execution_mode=execution_mode,
        **tool_options,
    )
