"""In-process agent execution — run one isolated agent turn directly in this
interpreter via Engine/LLM, instead of spawning a separate `tau` OS process.

Shared by the `subagent` tool and the `/workflow` extension: both need to run
an isolated agent turn given a task, an agent's tools/system prompt, and
(optionally) prior conversation history to resume read-only.

Deliberately narrow and self-contained: builds an Engine, an LLM, fresh tool
instances, and a Hooks() dispatcher directly — bypassing Runtime.create()'s
full bootstrap (resource discovery, extension loading, global registry
reload) entirely. That bootstrap mutates process-wide singletons
(skill_registry, prompt_registry, theme_registry) and re-fires session_start
on every extension, which is unsafe to run reentrantly from inside an
already-running session. Engine itself is explicitly documented as knowing
"nothing about sessions, extensions, or compaction" — exactly the isolation
this needs.

Trade-off: an embedded agent only has the base builtin coding tools
(read/write/edit/terminal/glob/grep/ls) plus whatever its own `tools:`
frontmatter names from that set — extension-contributed tools (web_search,
web_fetch, subagent, todo, ...) are not available, since loading them would
mean loading extensions. None of the shipped subagent presets need more than
the base set.

Fork context (resuming a parent session's history read-only) is safe for the
same reason SessionManager itself is safe to use here even though
Runtime.create() isn't: SessionManager is instance-scoped, not a mutated
process-wide singleton. Constructing one with persist=False and calling only
its read methods (build_session_context) never touches shared state and
never writes back — see load_fork_context().
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, create_model

from tau.builtins.tools import TOOLS
from tau.engine import Engine, EngineContext
from tau.hooks.engine import MessageEndEvent, ToolExecutionStartEvent
from tau.hooks.service import Hooks
from tau.inference import StopReason
from tau.inference.api.text.service import TextLLM
from tau.message.types import (
    AssistantMessage,
    LLMMessage,
    Role,
    ToolCallContent,
    ToolMessage,
    UserMessage,
)
from tau.tool.types import Tool, ToolContext, ToolInvocation, ToolKind, ToolResult

TASK_TIMEOUT_S = 300
_ABORT_GRACE_S = 15
_TOOL_ARG_KEYS = ("cmd", "pattern", "path")
_JSON_SCHEMA_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

# Same fallback Runtime.create() uses when no model is configured — matched
# here so an embedded agent behaves the same as a real session would.
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_PROVIDER = "anthropic"


def load_fork_context(cwd: Path, session_id: str, session_dir: Path) -> list[LLMMessage]:
    """Read a parent session's current branch as read-only LLM message history.

    Constructs a non-persisting SessionManager purely to read
    build_session_context() — never calls enable_persist() or any write
    method, so this cannot affect the parent session on disk. Filters out
    session-only message types (compaction/branch-summary markers, custom
    messages) that the LLM API layer doesn't understand, and drops any
    SystemMessage since the embedded agent supplies its own system_prompt
    separately.
    """
    from tau.session.manager import SessionManager

    matches = list(session_dir.rglob(f"*{session_id}*.jsonl"))
    if not matches:
        return []
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    session_file = matches[0].resolve()

    sm = SessionManager(cwd, session_dir=session_dir, session_file=session_file, persist=False)
    context = sm.build_session_context()
    keep = (UserMessage, AssistantMessage, ToolMessage)
    return [m for m in context.messages if isinstance(m, keep)]


def _tool_preview(name: str, args: dict[str, Any]) -> str:
    """One-line summary of a tool call, e.g. 'terminal: curl -s https://...'."""
    for key in _TOOL_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return f"{name}: {value}"[:100]
    for value in args.values():
        if isinstance(value, str) and value:
            return f"{name}: {value}"[:100]
    return name


def _build_tools(tool_names: list[str] | None, extra_tools: list[Tool] | None = None) -> list[Tool]:
    """Fresh instances of the requested base tools, plus any matching extra
    tools the caller already has instances of (e.g. web_search/web_fetch
    borrowed from the parent session's own tool registry — see
    subagent_tool.py's _extension_tools()). The full base set is used when
    tool_names is unset; extra_tools are only included if explicitly named.
    """
    selected = TOOLS if not tool_names else [t for t in TOOLS if t.name in set(tool_names)]
    tools = [t.__class__() for t in selected]  # type: ignore[call-arg]
    if tool_names and extra_tools:
        wanted = set(tool_names)
        tools += [t for t in extra_tools if t.name in wanted]
    return tools


def _json_schema_field_type(spec: Any) -> Any:
    """Map a flat JSON Schema field spec to a Python type. Supports string,
    integer, number, boolean, and array-of-those — enough for typical
    result shapes. Anything else (nested object, unset type) falls back to
    Any, which still validates but doesn't constrain."""
    if not isinstance(spec, dict):
        return Any
    t = spec.get("type")
    if t == "array":
        item_type = _json_schema_field_type(spec.get("items"))
        return list[item_type]  # type: ignore[valid-type]
    if not isinstance(t, str):
        return Any
    return _JSON_SCHEMA_TYPES.get(t, Any)


def build_schema_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Build a Pydantic model from a flat JSON-Schema-shaped dict:
    ``{"type": "object", "properties": {...}, "required": [...]}``.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError("schema must have a non-empty 'properties' mapping")
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for field_name, spec in properties.items():
        py_type = _json_schema_field_type(spec)
        fields[field_name] = (py_type, ...) if field_name in required else (py_type | None, None)
    return create_model(name, **fields)  # type: ignore[call-overload,no-any-return]


class StructuredOutputTool(Tool):
    """Terminating tool: one call ends the task, its (schema-validated) args
    become the task's output. Mirrors pi-dynamic-workflows' structured_output
    tool, built on Engine's native ToolResult.terminate mechanism.
    """

    def __init__(self, schema_model: type[BaseModel], capture: dict[str, Any]) -> None:
        self._capture = capture
        super().__init__(
            name="structured_output",
            description=(
                "Submit the final structured result for this task. Call this exactly once, "
                "as your last action, with fields matching the required shape. Do not also "
                "write a prose final answer — this call ends the task immediately."
            ),
            schema=schema_model,
            kind=ToolKind.Read,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Any = None,
        signal: Any = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        self._capture["called"] = True
        self._capture["value"] = invocation.params
        content = json.dumps(invocation.params)
        return ToolResult(
            id=invocation.id, content=content, terminate=True, terminate_message=content
        )


async def run_embedded_agent(
    *,
    cwd: Path,
    model_id: str | None,
    provider: str | None,
    system_prompt: str,
    tool_names: list[str] | None,
    task_text: str,
    schema: dict[str, Any] | None = None,
    initial_messages: list[LLMMessage] | None = None,
    abort_signal: asyncio.Event | None = None,
    extra_tools: list[Tool] | None = None,
    on_tool_start: Callable[[str], None] | None = None,
    on_event: Callable[[Any], None] | None = None,
    timeout_s: float = TASK_TIMEOUT_S,
) -> tuple[bool, str, dict[str, Any]]:
    """Run one agent turn to completion, bounded by ``timeout_s``.

    Returns (ok, output_text, usage). Fully self-contained: its own LLM
    instance, its own Hooks(), its own fresh tools — no session persistence,
    no shared registries, no OS subprocess.

    ``extra_tools``, if given, are pre-built Tool instances made available
    when ``tool_names`` explicitly names them — for tools this module can't
    construct itself (e.g. web_search/web_fetch need a configured search
    engine). Only used when named; the base coding toolset never needs this.

    ``initial_messages``, if given, is prepended to the conversation before
    the task message — use load_fork_context() to build this from a parent
    session's history for a read-only "fork" context.

    ``abort_signal``, if given, is used as the engine's own AbortSignal
    instead of a fresh one — set it externally (e.g. on parent-tool-call
    cancellation) to abort the run cooperatively. The timeout still applies
    on top of this and sets the same signal when it fires.

    When ``schema`` is set (a flat JSON-Schema-shaped dict), the task gets an
    extra ``structured_output`` tool and must call it exactly once to finish;
    its validated args (as JSON text) become the output. A task that finishes
    without calling it fails — this is meant for tasks whose output feeds a
    later placeholder substitution or handoff, where prose formatting habits
    (code fences, commentary) would otherwise break parsing.

    ``on_tool_start`` gets a one-line preview string per tool call, for
    simple progress logging. ``on_event`` (if set) gets every raw hook event
    object emitted during the run, for callers that want to build a richer
    live view (e.g. per-tool-call result previews).

    On timeout, aborts cooperatively via the engine's own AbortSignal (the
    same mechanism Esc-cancel uses in the interactive TUI) and gives it
    ``_ABORT_GRACE_S`` to unwind cleanly before falling back to raw task
    cancellation.
    """
    usage: dict[str, Any] = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    final_text = ""
    error_message: str | None = None
    failed = False

    try:
        llm = TextLLM(model_id=model_id or _DEFAULT_MODEL, provider=provider or _DEFAULT_PROVIDER)
    except Exception as e:
        return False, f"Failed to resolve model: {e}", usage

    tools = _build_tools(tool_names, extra_tools)
    structured_capture: dict[str, Any] | None = None
    if schema is not None:
        try:
            schema_model = build_schema_model("StructuredOutput", schema)
        except Exception as e:
            return False, f"Invalid task schema: {e}", usage
        structured_capture = {"called": False, "value": None}
        tools = [*tools, StructuredOutputTool(schema_model, structured_capture)]
        system_prompt = (
            system_prompt
            + "\n\nFinal output contract: your last action must be a structured_output "
            "tool call. Do not write a prose final answer instead."
        )
    known_tool_names = {t.name for t in tools}
    hooks = Hooks()

    def _on_message_end(event: MessageEndEvent) -> None:
        nonlocal final_text, error_message, failed
        message = event.message
        if getattr(message, "role", None) != Role.ASSISTANT:
            return
        usage["turns"] += 1
        usage["input_tokens"] += message.usage.input_tokens
        usage["output_tokens"] += message.usage.output_tokens
        usage["cost"] += message.usage.cost.total
        if message.stop_reason in (StopReason.Error, StopReason.Abort):
            failed = True
        if message.error:
            error_message = message.error
            failed = True
        text = message.text_content()
        if text:
            final_text = text

        # ToolExecutionStartEvent never fires for a tool name the model
        # invented (Engine._execute returns an error before emitting it), so
        # without this a model that hallucinates a tool and won't self-correct
        # would burn the whole timeout in visible silence — indistinguishable
        # from a genuine hang. Surface it explicitly instead.
        if on_tool_start is not None:
            for content in message.contents:
                if isinstance(content, ToolCallContent) and content.name not in known_tool_names:
                    on_tool_start(f"✗ unknown tool requested: {content.name!r} (ignored)")

    def _on_tool_start(event: ToolExecutionStartEvent) -> None:
        if on_tool_start is not None:
            on_tool_start(_tool_preview(event.tool_call.name, event.tool_call.args))

    hooks.register("message_end", _on_message_end)
    hooks.register("tool_execution_start", _on_tool_start)
    if on_event is not None:
        hooks.subscribe(on_event)

    engine = Engine(cwd=cwd, llm=llm, tools=tools, system_prompt=system_prompt, hooks=hooks)
    task_message = UserMessage.from_text(f"Task: {task_text}")
    messages: list[LLMMessage] = [*(initial_messages or []), task_message]
    ctx = EngineContext(system_prompt=system_prompt, messages=messages, tools=tools)
    signal = abort_signal if abort_signal is not None else asyncio.Event()
    run_task = asyncio.ensure_future(engine.run(ctx, signal=signal))

    try:
        await asyncio.wait_for(asyncio.shield(run_task), timeout=timeout_s)
    except asyncio.CancelledError:
        # External cancellation (e.g. the parent engine's tool timeout hit
        # before TASK_TIMEOUT_S). shield() keeps run_task alive, so cancel it
        # explicitly and wait for it to unwind — otherwise the orphaned engine
        # keeps streaming and executing tools with its results discarded.
        signal.set()
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run_task
        raise
    except TimeoutError:
        signal.set()
        try:
            await asyncio.wait_for(run_task, timeout=_ABORT_GRACE_S)
        except TimeoutError:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
        return False, f"Task timed out after {timeout_s:.0f}s", usage
    except Exception as e:
        return False, f"Subagent failed: {e}", usage

    if failed:
        return False, error_message or final_text or "(no output)", usage

    if structured_capture is not None:
        if not structured_capture["called"]:
            return False, "Task required structured output but finished without calling it", usage
        return True, json.dumps(structured_capture["value"]), usage

    return True, final_text or "(no output)", usage
