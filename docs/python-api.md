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
| `initial_messages` | `list[AgentMessage]` | Existing conversation messages appended at startup |
| `initial_prompt` | `str \| None` | Optional startup user-message text |
| `initial_images` | `list[Any]` | Images attached to the startup user message |
| `initial_audio` | `list[str \| bytes]` | Audio attached to the startup user message |
| `initial_video` | `list[str \| bytes]` | Video attached to the startup user message |
| `mode` | `str` | `"interactive"`, `"print"`, `"json"`, or `"rpc"` |
| `tools` | `list[Tool]` | Extra tools registered as `"runtime"` source |
| `tool_allowlist` | `set[str] \| None` | Enable only these built-in, runtime, or extension tool names |
| `exclude_tools` | `set[str]` | Tool names disabled after applying the allowlist |
| `system_prompt` | `str` | Custom system prompt (overrides the default) |
| `resource_loader` | `ResourceLoader \| None` | Replace resource discovery and registry loading |
| `dependencies` | `RuntimeDependencies` | Factories for settings, LLM, sessions, hooks, and tool registry |
| `config_dir` | `Path \| None` | Override config directory (default `~/.tau`) |

## `Runtime`

### Factory

```python
runtime = await Runtime.create(config)
```

`create()` builds the full dependency graph: settings, LLM, session manager, extensions, tool registry, engine, and agent.

### Initial messages and media

Seed an existing conversation and an optional media-bearing user message:

```python
from tau.message.types import AssistantMessage, UserMessage

config = RuntimeConfig(
    cwd=Path.cwd(),
    initial_messages=[
        UserMessage.from_text("We are reviewing the API."),
        AssistantMessage.from_text("Understood."),
    ],
    initial_prompt="Review this screenshot",
    initial_images=[Path("screen.png").read_bytes()],
)
runtime = await Runtime.create(config)
```

Initial messages are appended to the session before the runtime emits
`session_start`. Supplying any initial media creates a user message even when
`initial_prompt` is omitted. Runtime creation does not automatically invoke the
model; call `runtime.invoke()` when a response is required.

The startup seed is consumed once by `Runtime.create()` and is not repeated when
that runtime creates or resumes another session. Calling `RuntimeContext.create()`
directly applies the seed on every call.

### Dependency injection

Use `RuntimeDependencies` to replace services constructed by the runtime:

```python
from tau.hooks.service import Hooks
from tau.inference.api.text.service import TextLLM
from tau.runtime.dependencies import RuntimeDependencies

shared_hooks = Hooks()

def create_llm(context):
    return TextLLM(
        model_id=context.model_id,
        provider=context.provider,
        models=my_model_registry,
        providers=my_provider_registry,
        apis=my_api_registry,
        auth_manager=my_auth_manager,
    )

config = RuntimeConfig(
    cwd=Path.cwd(),
    dependencies=RuntimeDependencies(
        llm=create_llm,
        hooks=lambda: shared_hooks,
    ),
)
runtime = await Runtime.create(config)
```

Available factories:

| Factory | Context | Purpose |
|---------|---------|---------|
| `settings` | `SettingsFactoryContext` | Construct project/global settings access |
| `llm` | `LLMFactoryContext` | Construct the text LLM, including custom model, provider, API, or auth registries |
| `session_manager` | `SessionManagerFactoryContext` | Select persistent, in-memory, or custom session storage |
| `hooks` | None | Provide the shared lifecycle event bus |
| `tool_registry` | None | Provide the tool registry |

LLM, session-manager, and tool-registry factories run again when Tau replaces
the active session. The existing settings manager and hook bus are preserved
across those replacements. The LLM factory is also used by `set_model()`.

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

### Events and queued messages

```python
unsubscribe = runtime.subscribe(lambda event: print(event.type))

await runtime.steer("Use the database implementation instead")
await runtime.follow_up("Run the integration tests afterward")

unsubscribe()
```

`steer()` queues a message for the active turn after its current tool round.
`follow_up()` queues a message for delivery after the active turn finishes.

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

`DefaultResourceLoader` also supports focused overrides without subclassing:

```python
loader = DefaultResourceLoader(
    skills_override=lambda current: (*current, Path("shared-skills")),
    context_files_override=lambda current: current,
    system_prompt_override=lambda: "Use the project engineering standards.",
)
```

Available callbacks are `extensions_override`, `skills_override`,
`prompts_override`, `themes_override`, `context_files_override`, and
`system_prompt_override`.

Context files are represented by `ContextFile` objects and included in the
`ResourceSnapshot` alongside structured diagnostics. Inspect the latest
diagnostics through `runtime.resource_diagnostics`.

`DefaultResourceLoader` reports:

- Missing or invalid configured extension paths
- Missing installed-package directories
- Malformed package manifests and missing declared package resources
- Package resource selectors that match nothing
- Missing hook-contributed or override paths
- Context files that could not be read

Each `ResourceDiagnostic` includes `"warning"` or `"error"` severity, a source,
message, and optional path. Diagnostics do not stop startup; extension loading
continues with valid resources.

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
