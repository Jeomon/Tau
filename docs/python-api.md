# Python API

This page covers using tau programmatically — embedding the agent in your own applications or scripts.

## Core Entry Point

The main programmatic API is `Runtime`. Create one from a `RuntimeConfig`, then call `invoke()`:

```python
import asyncio
from pathlib import Path
from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig

async def main():
    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        persist_session=False,
    )
    runtime = await Runtime.create(config)
    await runtime.invoke("Summarize the README.md file")

asyncio.run(main())
```

## `RuntimeConfig`

`RuntimeConfig` is a Pydantic model. All fields are optional except `cwd`.

| Field | Type | Description |
|-------|------|-------------|
| `cwd` | `Path` | Working directory for the session |
| `model_id` | `str \| None` | Model ID (falls back to settings, then default) |
| `provider` | `str \| None` | Provider ID (falls back to settings) |
| `session_file` | `Path \| None` | Resume from an existing session file |
| `persist_session` | `bool` | Save session to disk (default `True`) |
| `resume` | `bool` | Resume the most recent session for `cwd` (default `False`) |
| `mode` | `str` | `"interactive"`, `"print"`, `"json"`, or `"rpc"` |
| `tools` | `list[Tool]` | Extra tools registered as `"runtime"` source |
| `system_prompt` | `str` | Custom system prompt (overrides the default) |
| `resource_loader` | `ResourceLoader \| None` | Replace resource discovery and registry loading |
| `config_dir` | `Path \| None` | Override config directory (default `~/.tau`) |

## `Runtime`

### Factory

```python
runtime = await Runtime.create(config)
```

`create()` builds the full dependency graph: settings, LLM, session manager, extensions, tool registry, engine, and agent.

### Invoking the agent

```python
await runtime.invoke("Your prompt here")
```

For file references, prepend file content to the message yourself or use `@path` syntax (TUI only).

### Session management

```python
await runtime.new_session()               # start fresh
await runtime.resume_session(path)        # switch to an existing session file
await runtime.fork_session(entry_id)      # branch at a session entry
```

### Extension reload

```python
await runtime.reload_extensions()
```

Re-discovers all extensions, skills, and prompts; syncs tools and rebuilds the system prompt without creating a new session.

## Custom Resource Loaders

`RuntimeConfig.resource_loader` accepts any object implementing the
`ResourceLoader` protocol. Tau passes a `ResourceContext` containing the current
working directory, settings manager, and hook bus on startup and reload.

Subclassing the default loader is the simplest way to customize discovery while
retaining Tau's extension and registry behavior:

```python
from dataclasses import replace

from tau.resources import (
    DefaultResourceLoader,
    ResourceContext,
    ResourceSnapshot,
)

class ProjectResourceLoader(DefaultResourceLoader):
    async def discover(self, context: ResourceContext) -> ResourceSnapshot:
        snapshot = await super().discover(context)
        return replace(
            snapshot,
            skill_paths=(*snapshot.skill_paths, context.cwd / "agent-skills"),
        )

loader = ProjectResourceLoader()
config = RuntimeConfig(cwd=Path.cwd(), resource_loader=loader)
runtime = await Runtime.create(config)
```

A loader may instead implement all three protocol methods directly:
`discover()`, `create_extension_loader()`, and `apply_registries()`. The same
loader instance is retained for `/reload`.

### Model switching

```python
await runtime.set_model("claude-opus-4-8", provider="anthropic")
```

### Shell commands

```python
await runtime.execute_bash("git status")
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `runtime.agent` | `Agent \| None` | The active Agent instance |
| `runtime.hooks` | `Hooks` | The shared hook bus |
| `runtime.session_manager` | `SessionManager` | The active session manager |
| `runtime.settings_manager` | `SettingsManager` | Settings access |
| `runtime.extension_runtime` | `ExtensionRuntime \| None` | Loaded extensions |

## Listening to Events

Subscribe to the hook bus directly to observe what the agent does:

```python
from tau.hooks.types import MessageEndEvent, SettledEvent

async def on_message_end(event: MessageEndEvent):
    print("Response:", event.message)

unsub = runtime.hooks.register("message_end", on_message_end)

await runtime.invoke("Hello")

unsub()  # remove the handler
```

`Hooks.register(event_type, handler)` returns an unsubscribe callable.

### Useful events for programmatic use

| Event | When |
|-------|------|
| `message_end` | Model response fully received |
| `tool_execution_end` | A tool call finished |
| `agent_end` | The current low-level engine loop ended; post-run processing may remain |
| `settled` | The invocation completed post-run processing with no messages currently queued |

## Custom Tools at Runtime

Pass tools in `RuntimeConfig.tools` to make them available from the start:

```python
from tau.tool.types import Tool, ToolKind, ToolInvocation, ToolResult
from pydantic import BaseModel, Field

class _Schema(BaseModel):
    expression: str = Field(..., description="Math expression to evaluate")

class CalculatorTool(Tool):
    def __init__(self):
        super().__init__(
            name="calculator",
            description="Evaluate a math expression.",
            schema=_Schema,
            kind=ToolKind.Execute,
        )

    async def execute(self, invocation, tool_execution_update_callback=None, signal=None, context=None):
        try:
            result = eval(invocation.params["expression"], {"__builtins__": {}})
            return ToolResult.ok(invocation.id, str(result))
        except Exception as e:
            return ToolResult.error(invocation.id, str(e))

config = RuntimeConfig(cwd=Path.cwd(), tools=[CalculatorTool()])
runtime = await Runtime.create(config)
```

## `ToolRegistry`

The `ToolRegistry` is the single source of truth for all registered tools. It tracks tools by source and can sync the live engine:

```python
registry = runtime._context.tool_registry

# Inspect
all_tools = registry.list()
ext_tools  = registry.list(source="extension")
names      = registry.names()

# Mutate (then sync to the engine)
registry.register(MyTool(), source="custom")
registry.sync_to_engine(runtime.agent._engine)
```

Sources: `"builtin"`, `"runtime"`, `"extension"`.

## `Agent`

`runtime.agent` exposes the lower-level session agent:

```python
agent = runtime.agent

# Check state
agent.is_idle()
agent.phase             # AgentPhase.IDLE, TURN, COMPACTION, or BRANCH_SUMMARY
agent.streaming_message
agent.pending_tool_call_ids
agent.error_message
agent.queued_messages
agent.get_context_usage()   # ContextUsage(tokens, context_window, percent)
agent.get_system_prompt()

# Wait through save-point handlers and post-run compaction
await agent.wait_for_idle()

# Abort
agent.abort()

# Manual compaction
await agent.compact()
```

## Headless / Print Mode

For scripting without the TUI, use `mode="print"` and drive via `invoke()`:

```python
config = RuntimeConfig(cwd=Path.cwd(), mode="print", persist_session=False)
runtime = await Runtime.create(config)

last_response = None

async def capture(event):
    global last_response
    from tau.message.types import AssistantMessage
    if hasattr(event, "message") and isinstance(event.message, AssistantMessage):
        last_response = event.message

runtime.hooks.register("message_end", capture)
await runtime.invoke("What is 2 + 2?")
print(last_response)
```

## Example: Batch Processing

```python
import asyncio
from pathlib import Path
from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig
from tau.message.types import AssistantMessage
from tau.hooks.types import SettledEvent

async def review_files(files: list[str]) -> dict[str, str]:
    config = RuntimeConfig(cwd=Path.cwd(), persist_session=False)
    runtime = await Runtime.create(config)
    results = {}

    for file_path in files:
        result_text = []
        settled = asyncio.Event()

        async def on_msg(event):
            if hasattr(event, "message") and isinstance(event.message, AssistantMessage):
                for c in event.message.contents:
                    if hasattr(c, "content"):
                        result_text.append(c.content)

        async def on_settled(_):
            settled.set()

        u1 = runtime.hooks.register("message_end", on_msg)
        u2 = runtime.hooks.register("settled", on_settled)

        await runtime.invoke(f"Review {file_path} for bugs.")
        await settled.wait()

        u1(); u2()
        results[file_path] = "".join(result_text)
        await runtime.new_session()

    return results

reviews = asyncio.run(review_files(["app.py", "utils.py"]))
```

## Next Steps

- [Extensions](extensions.md) — Extend tau with custom tools and commands
- [Architecture](architecture.md) — System design
- [Settings](settings.md) — Configuration reference
