# Engine

`tau.engine` is Tau's standalone text-inference and tool-execution loop. It can
be embedded directly when an application needs agentic model/tool iteration
without Tau's sessions, compaction, extensions, runtime lifecycle, or terminal
UI.

Use [Inference](inference.md) instead when the application only needs a single
model request and will manage tool calls itself. Use the
[Python API](python-api.md) when the application needs the complete Tau runtime.

## Responsibilities

The engine:

- streams normalized text inference events
- accumulates assistant messages
- validates and executes requested tools
- runs safe tool batches sequentially or concurrently
- emits message, turn, tool, and engine lifecycle events
- supports steering, follow-up messages, continuation, and cancellation

The engine does not persist messages, compact context, discover extensions,
construct the application system prompt, or manage the user interface.
`tau.agent` owns session-aware orchestration around the engine, while
`tau.runtime` constructs and connects the full application.

## Public API

| Type | Purpose |
|------|---------|
| `Engine` | Streaming inference and tool-execution service |
| `EngineContext` | System prompt, message history, and tools for one run |
| `EngineOptions` | Execution strategy and lifecycle callbacks |
| `EngineState` | Observable mutable execution state |

The former `Agent` and `AgentState` names remain compatibility aliases, and
`AgentOptions` is the descriptive compatibility name for `EngineOptions`.
New code should use the explicit `Engine` names.

## Basic Example

```python
import asyncio
from pathlib import Path

from tau.engine import Engine, EngineContext, MessageEndEvent
from tau.inference.api.text.service import TextLLM
from tau.message.types import UserMessage


async def main() -> None:
    llm = TextLLM("gpt-4o")
    engine = Engine(cwd=Path.cwd(), llm=llm, tools=[])

    def print_completed_message(event: object) -> None:
        if isinstance(event, MessageEndEvent):
            print(event.message)

    await engine.subscribe(print_completed_message)
    await engine.run(
        EngineContext(
            system_prompt="Answer concisely.",
            messages=[UserMessage.from_text("What does an execution engine do?")],
        )
    )


asyncio.run(main())
```

Configure the model provider before running the example. See
[Inference Providers](inference-providers.md).

## Supplying Tools

Pass the same tool objects to both the engine and the run context:

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

Tools declare their own execution safety. A batch runs concurrently only when
every tool in that batch permits parallel execution; otherwise source order is
preserved.

## Lifecycle Control

- `subscribe(handler)` observes engine events and returns an unsubscribe
  callback.
- `steer(message)` injects guidance during an active tool loop.
- `follow_up(message)` queues another user message after a natural stop.
- `abort()` cancels the active stream at the next safe boundary.
- `run_continue()` resumes from the engine's current in-memory history.
- `wait_for_idle()` waits until the active loop exits.

Engine history is in memory only. Applications using the engine directly are
responsible for durable storage and for constructing the next
`EngineContext`.

## Dependency Boundary

`tau.engine` depends on inference, messages, tools, hooks, and optional
settings. It must not import session, extension, TUI, runtime, or agent
services. This keeps the engine independently testable and embeddable.
