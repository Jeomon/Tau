> `tau.engine` is embeddable on its own. It needs an LLM and a list of tools — nothing else from Tau.

# Engine

`tau.engine` is Tau's standalone inference and tool-execution loop. It streams an
assistant message from a model, executes whatever tools the model asks for, feeds
the results back, and repeats until the model stops. Embed it when an application
needs agentic model/tool iteration without Tau's sessions, compaction, extensions,
runtime lifecycle, or terminal UI.

Use [Inference](inference.md) instead when the application only needs a single
model request and will manage tool calls itself. Use the
[Python API](python-api.md) when the application needs the complete Tau runtime.

## Table of Contents

- [Responsibilities](#responsibilities)
- [Public API](#public-api)
- [Standalone Usage](#standalone-usage)
- [Engine](#engine)
- [EngineContext](#enginecontext)
- [EngineOptions](#engineoptions)
- [EngineState](#enginestate)
- [Events](#events)
- [Tool Execution](#tool-execution)
- [Lifecycle Control](#lifecycle-control)
- [Dependency Boundary](#dependency-boundary)

## Responsibilities

The engine:

- streams normalized text inference events into accumulating `AssistantMessage` objects
- validates tool arguments and executes requested tools
- schedules tool batches sequentially or concurrently based on each tool's declared mode
- enforces per-call tool timeouts, a parallelism cap, and abort boundaries
- emits agent, turn, message, and tool lifecycle events to hooks and subscribers
- supports steering, follow-up messages, continuation, and cancellation

The engine does not persist messages, compact context, discover extensions,
construct the application system prompt, or manage the user interface.
`tau.agent` owns session-aware orchestration around the engine, and
`tau.runtime` constructs and connects the full application. See
[Architecture](architecture.md) for how the layers stack.

## Public API

Everything below is exported from `tau.engine`.

| Name | Kind | Purpose |
|------|------|---------|
| `Engine` | class | Streaming inference and tool-execution service |
| `EngineContext` | dataclass | System prompt, message history, and tools for one run |
| `EngineOptions` | dataclass | Execution strategy, limits, and lifecycle callbacks |
| `EngineState` | dataclass | Observable mutable execution state |
| `AgentEvent` | union | Every event the engine can emit |
| `AgentEventType` | `StrEnum` | Event type identifier strings |
| `SteeringMode` / `FollowupMode` | `StrEnum` | Queue drain behavior (`one_at_a_time`, `all`) |
| `SteeringQueue` / `FollowupQueue` | dataclass | FIFO queues for injected messages |
| `ToolExecutionMode` | `StrEnum` | Re-exported from `tau.tool.types` |

### Compatibility Aliases

`tau/engine/__init__.py` still binds the original names. They are the *same
objects*, not subclasses:

| Alias | Current name |
|-------|--------------|
| `Agent` | `Engine` |
| `AgentState` | `EngineState` |
| `AgentOptions` | `EngineOptions` |

New code should use the `Engine*` names. Note that `tau.agent.Agent` is a
**different, real class** — the session-aware orchestrator — not an alias. Do not
confuse `from tau.engine import Agent` with `from tau.agent import Agent`.

`tau.agent.types.AgentContext` is an alias for `EngineContext`.

## Standalone Usage

A complete, copy-pasteable script. It needs only a configured model provider —
see [Inference Providers](inference-providers.md) for credentials.

```python
import asyncio
from pathlib import Path

from tau.engine import (
    AgentEvent,
    Engine,
    EngineContext,
    EngineOptions,
    MessageEndEvent,
    ToolExecutionEndEvent,
)
from tau.inference.api.text.service import TextLLM
from tau.message.types import UserMessage


async def main() -> None:
    llm = TextLLM("claude-sonnet-4-5-20250929")

    engine = Engine(
        cwd=Path.cwd(),
        llm=llm,
        tools=[],
        options=EngineOptions(
            tool_timeout_seconds=60.0,
            max_parallel_tool_calls=4,
        ),
    )

    async def on_event(event: AgentEvent) -> None:
        match event:
            case MessageEndEvent(message=message) if message is not None:
                print("assistant:", message.text_content())
            case ToolExecutionEndEvent(tool_result=result):
                print(f"tool {result.tool_name} -> error={result.is_error}")

    unsubscribe = await engine.subscribe(on_event)

    await engine.run(
        EngineContext(
            system_prompt="Answer concisely.",
            messages=[UserMessage.from_text("What does an execution engine do?")],
        )
    )

    unsubscribe()
    print("turns recorded:", len(engine.state.messages))


asyncio.run(main())
```

What this does **not** do: nothing is written to disk, no session file is
created, context is never compacted, and no extension or slash command is
loaded. `engine.state.messages` is in-memory only. Applications embedding the
engine own durable storage and construction of the next `EngineContext`.

## Engine

```python
Engine(
    cwd: Path,
    llm: TextLLM,
    tools: list[Tool],
    system_prompt: str | None = None,
    options: EngineOptions | None = None,
    hooks: Hooks | None = None,
    settings: SettingsManager | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cwd` | `Path` | required | Working directory placed on the `ToolContext` given to every tool |
| `llm` | `TextLLM` | required | Text inference client used for each turn |
| `tools` | `list[Tool]` | required | Tools available for dispatch; indexed by `Tool.name` |
| `system_prompt` | `str \| None` | `None` | Default prompt; `EngineContext.system_prompt` overrides per run |
| `options` | `EngineOptions \| None` | `EngineOptions()` | Behavior knobs and callbacks |
| `hooks` | `Hooks \| None` | `Hooks()` | Hook bus events are emitted onto |
| `settings` | `SettingsManager \| None` | `None` | Optional settings exposed to tools via `ToolContext` |

### Methods

| Method | Description |
|--------|-------------|
| `await run(ctx, signal=None)` | Run a fresh loop from `ctx` |
| `await run_continue(signal=None)` | Resume from the engine's current in-memory history |
| `await subscribe(handler)` | Register a sync or async event handler; returns an unsubscribe callable |
| `await steer(message)` | Enqueue a message injected after the next tool-call round-trip |
| `await follow_up(message)` | Enqueue a message injected after the loop stops naturally |
| `clear_steering()` / `clear_follow_up()` / `clear_all_queues()` | Discard queued messages |
| `has_pending_messages()` | `True` if either queue is non-empty |
| `abort()` | Signal the loop to stop at the next safe check point |
| `is_idle` (property) | `True` when no streaming loop is active |
| `await wait_for_idle()` | Wait until the streaming loop exits |
| `reset()` | Clear transient turn state so the engine can be re-run after an error |
| `set_llm(llm)` | Swap the active model; raises `RuntimeError` while streaming |
| `await process_events(event)` | Apply an event to state, then fan it out to hooks and subscribers |

## EngineContext

Inputs for one run. `tau.agent.types.AgentContext` aliases this type.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `system_prompt` | `str` | required | System prompt for this run |
| `messages` | `list[LLMMessage]` | required | Full history to send; see [Messages](messages.md) |
| `tools` | `list[Tool]` | `[]` | Tools advertised to the model for this run |

Pass the same tool objects to both the constructor and the context. The
constructor's list builds the dispatch table; the context's list is what the
model is told about.

```python
tools = [read_tool, terminal_tool]
engine = Engine(cwd=Path.cwd(), llm=llm, tools=tools)

await engine.run(
    EngineContext(
        system_prompt="Use tools when needed.",
        messages=[UserMessage.from_text("Inspect this project.")],
        tools=tools,
    )
)
```

## EngineOptions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `before_tool_call` | callback | `None` | Rewrite a `ToolInvocation`, or short-circuit it by returning a `ToolResultContent` |
| `after_tool_call` | callback | `None` | Inspect or replace a `ToolResult` |
| `on_event` | callback | `None` | Async handler invoked before registered subscribers |
| `execution_mode` | `ToolExecutionMode \| None` | `None` | Force a scheduling strategy; `None` means per-batch decision |
| `steering_mode` | `SteeringMode` | `OneAtATime` | Drain one steering message per boundary, or all |
| `followup_mode` | `FollowupMode` | `OneAtATime` | Drain one follow-up message per boundary, or all |
| `get_steering_messages` | callback | `None` | Pull steering messages from an external source |
| `get_follow_up_messages` | callback | `None` | Pull follow-up messages from an external source |
| `should_stop_after_turn` | callback | `None` | Return `True` to end the loop after a turn |
| `should_skip_tool_calls` | callback | `None` | Return a `ToolResultContent` to skip a call without executing it |
| `transform_context` | callback | `None` | Rewrite the message list immediately before each request |
| `ephemeral_injection` | callback | `None` | Return `UserMessage` objects injected for one turn only |
| `tool_timeout_seconds` | `float \| None` | `120.0` | Per-`Tool.execute()` timeout; `None` disables |
| `max_parallel_tool_calls` | `int \| None` | `10` | Concurrency cap; `None` means unbounded |
| `event_handler_timeout_seconds` | `float \| None` | `10.0` | Per-handler event timeout; `None` disables |

Set `tool_timeout_seconds` to `None` for embedded clients with intentionally
long-running tools. A handler that exceeds `event_handler_timeout_seconds` is
logged and abandoned; it never blocks the tool lifecycle.

## EngineState

`engine.state` is a live, mutable dataclass. Read it; treat writes as unsupported
except through engine methods.

| Field | Type | Description |
|-------|------|-------------|
| `system_prompt` | `str \| None` | Prompt in effect |
| `messages` | `list[LLMMessage]` | In-memory history, appended on `MessageEndEvent` |
| `pending_tool_calls` | `set[str]` | Tool-call ids currently executing |
| `is_streaming` | `bool` | Loop active |
| `idle_event` | `asyncio.Event` | Set while idle; backs `wait_for_idle()` |
| `llm` | `TextLLM \| None` | Active inference client |
| `streaming_message` | `AssistantMessage \| None` | Partial message during streaming |
| `thinking_level` | `ThinkingLevel \| None` | Reasoning level for the next request |
| `error_message` | `str \| None` | Set from `AgentErrorEvent` |
| `tools` | `list[Tool]` | Tools registered at construction |
| `steering_queue` / `follow_up_queue` | queue | Injected-message queues |

## Events

Every event is a dataclass with a literal `type` field. The `AgentEventType`
enum holds the same strings.

| Event class | `type` | Payload |
|-------------|--------|---------|
| `AgentStartEvent` | `agent_start` | — |
| `AgentEndEvent` | `agent_end` | `messages`, `reason` (`AgentEndReason`) |
| `AgentErrorEvent` | `agent_error` | `error` |
| `TurnStartEvent` | `turn_start` | `turn_index`, `timestamp` |
| `TurnEndEvent` | `turn_end` | `turn_index`, `message`, `tool_results` |
| `MessageStartEvent` | `message_start` | `message` |
| `MessageUpdateEvent` | `message_update` | `message` |
| `MessageEndEvent` | `message_end` | `message` |
| `MessageRollbackEvent` | `message_rollback` | `count` |
| `ToolExecutionStartEvent` | `tool_execution_start` | `tool_call` |
| `ToolExecutionUpdateEvent` | `tool_execution_update` | `partial_tool_result` |
| `ToolExecutionEndEvent` | `tool_execution_end` | `tool_result` |
| `ToolExecutionFailureEvent` | `tool_execution_failure` | `tool_name`, `tool_call_id`, `input`, `error` |

The classes live in `tau/hooks/engine.py` and are re-exported by `tau.engine`,
with two exceptions: `MessageRollbackEvent` and `ToolExecutionFailureEvent` are
members of the `AgentEvent` union but are **not** in `tau.engine.__all__`. Import
those two from `tau.hooks.engine`.

`AgentEndReason` is `completed`, `aborted`, or `error`.

`MessageRollbackEvent` retracts the last `count` committed messages. It fires
when an interrupted tool turn is discarded — the assistant tool-call message and
its tool-result message were already committed before the abort landed.

## Tool Execution

`ToolExecutionMode` on each `Tool` declares how the engine may schedule it.

| Mode | Behavior |
|------|----------|
| `Sequential` | Calls run one at a time in assistant source order |
| `Parallel` | Calls may run concurrently, bounded by `max_parallel_tool_calls` |
| `Batch` | Decide per batch (the default when `EngineOptions.execution_mode` is `None`) |

Batch scheduling is all-or-nothing: a batch runs concurrently only when *every*
tool in it declares `Parallel`. A single sequential tool is an ordering barrier
for the whole batch, because running parallel tools around it could reorder
observable side effects. Results are always returned in source order regardless
of execution order.

`kind` and `execution_mode` are constructor arguments on `Tool`, not class
attributes. `execution_mode` defaults to `Sequential`, so a tool must opt in to
concurrency. `kind` (`ToolKind`: `read`, `edit`, `write`, `execute`, `web`) is
the semantic category used to apply execution policy. See
[Creating Tools](creating-tools.md).

Failures are contained per call. A tool that raises produces an error
`ToolResultContent` for that call only; sibling calls in the batch continue. A
call that exceeds `tool_timeout_seconds` is cancelled at the engine boundary even
if the tool ignores the abort signal.

## Lifecycle Control

```python
await engine.steer(UserMessage.from_text("Focus on the parser only."))
await engine.follow_up(UserMessage.from_text("Now write tests."))
engine.abort()
await engine.wait_for_idle()
await engine.run_continue()
```

- **Steering** messages are injected after the next tool-call round-trip, while
  the loop is still running.
- **Follow-up** messages are injected after the loop reaches a natural stop.
- Both drain according to `steering_mode` / `followup_mode`.
- `run_continue()` resumes from `engine.state.messages` without a new
  `EngineContext`.

## Dependency Boundary

`tau.engine` imports from inference, messages, tools, hooks, and optionally
settings. It must not import session, extension, TUI, runtime, or agent modules.
This is what keeps the engine independently testable and embeddable.

## Next Steps

- [Architecture](architecture.md) - how engine, agent, and runtime layer
- [Messages](messages.md) - message and content-block types
- [Creating Tools](creating-tools.md) - implementing `Tool`
- [Inference](inference.md) - the layer below the engine
