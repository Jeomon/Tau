# Python API

The Python API gives you programmatic access to Tau's agent. Use it to embed Tau in another application, script batch workflows, or drive the agent without the terminal UI.

`Runtime` is the top-level entry point: it owns one agent session and everything under it (settings, LLM, session storage, tools, extensions, engine). For the lower-level streaming/tool-execution loop without session management, see [Engine](engine.md).

## Table of Contents

- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Options Reference](#options-reference)
- [Runtime API](#runtime-api)
- [Custom Tools](#custom-tools)
- [Inline Extensions](#inline-extensions)
- [Custom Resource Loaders](#custom-resource-loaders)
- [Complete Example](#complete-example)
- [Exports](#exports)

## Quick Start

```python
import asyncio
from pathlib import Path

from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig


async def main() -> None:
    config = RuntimeConfig(
        cwd=Path.cwd(),
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        persist_session=False,
    )
    runtime = await Runtime.create(config)
    try:
        await runtime.invoke("Summarize README.md")
    finally:
        await runtime.ashutdown()


asyncio.run(main())
```

`Runtime.create()` builds the whole dependency graph and fires `session_start`. It does not call the model. `invoke()` does.

> **Always `await runtime.ashutdown()`.** It emits `runtime_stop`, cancels background tasks (version check, telemetry, local model discovery), and lets extensions reap subprocesses. It is idempotent.

## Core Concepts

### Runtime

`Runtime` (`tau.runtime.service`) orchestrates one session's lifecycle: creation, switching, forking, tree navigation, and shutdown. It holds a `RuntimeContext` internally and replaces it whenever the active session changes.

```python
runtime = await Runtime.create(config)

runtime.agent              # Agent | None      — the session agent
runtime.session_manager    # SessionManager    — session storage and tree
runtime.settings_manager   # SettingsManager | None
runtime.hooks              # Hooks             — shared event bus
runtime.extension_runtime  # ExtensionRuntime | None
runtime.commands           # CommandRegistry   — slash commands
```

Session-replacing operations (`new_session()`, `resume_session()`, `clone_session()`) rebuild the internal context. The `Runtime` object itself stays valid, but `runtime.agent` and `runtime.session_manager` return new objects afterwards. The settings manager, hook bus, and extension runtime are carried across.

### RuntimeConfig

`RuntimeConfig` (`tau.runtime.types`) is a Pydantic model, an immutable configuration snapshot. `cwd` is the only required field. See the [Options Reference](#options-reference) for every field.

```python
from tau.runtime.types import RuntimeConfig

config = RuntimeConfig(cwd=Path.cwd())
```

### RuntimeStartupResult

`Runtime.create_with_result()` returns a `RuntimeStartupResult` instead of a bare runtime, surfacing what went wrong during startup without raising.

```python
result = await Runtime.create_with_result(config)
runtime = result.runtime

if result.has_issues:
    for diagnostic in result.resource_diagnostics:
        print(diagnostic.severity, diagnostic.message, diagnostic.path)
    for error in result.extension_errors:
        print(error.extension_path, error.error)

if result.model_fallback_reason:
    print("Model note:", result.model_fallback_reason)
```

| Field | Type | Description |
|-------|------|-------------|
| `runtime` | `Runtime` | The fully initialized runtime |
| `resource_diagnostics` | `tuple[ResourceDiagnostic, ...]` | Resource-discovery warnings and errors |
| `extension_errors` | `tuple[ExtensionError, ...]` | File and inline extension load failures |
| `requested_model_id` | `str` | Model requested after config/settings/default resolution |
| `requested_provider_id` | `str \| None` | Provider requested after the same resolution |
| `selected_model_id` | `str` | Model actually constructed |
| `selected_provider_id` | `str` | Provider actually constructed |
| `model_fallback_reason` | `str \| None` | Why a different model/provider was selected, else `None` |
| `has_issues` | `bool` (property) | `True` when any diagnostic or extension error was reported |

Tau does not silently substitute a different model ID. `TextLLM` may skip an unavailable provider variant of the same model, and custom LLM factories can expose their own `fallback_reason`.

### Model Resolution

The active model is resolved in this order:

1. `RuntimeConfig.model_id` / `RuntimeConfig.provider`
2. The `text` model reference stored in settings
3. `"claude-sonnet-4-6"` on provider `"anthropic"`

`base_url` and `thinking_level` are per-run overrides applied on top of the resolved model. Neither is written back to settings. The thinking level is clamped to what the model actually supports.

### Events

Every lifecycle signal flows through one `Hooks` bus. Register for a specific event type, or subscribe to all of them.

```python
from tau.hooks.types import MessageEndEvent

async def on_message_end(event: MessageEndEvent) -> None:
    print("Response:", event.message)

unsubscribe = runtime.hooks.register("message_end", on_message_end)
await runtime.invoke("Hello")
unsubscribe()
```

Handlers registered on the bus take exactly one argument: the event. (Extension handlers declared with `@tau.on(...)` receive `(event, context)` instead; that second argument is supplied by the extension layer, not by `Hooks`.) Handlers may be sync or async, and a raising handler is logged and skipped rather than propagated.

`runtime.subscribe(listener)` receives *every* event and returns the same unsubscribe callable.

| Event | When |
|-------|------|
| `runtime_start` | Earliest startup signal, before extensions load |
| `runtime_ready` | Runtime fully wired, before any mode loop begins |
| `session_start` | A session started (startup, new, resume, fork, clone) |
| `message_end` | A model response was fully received |
| `tool_execution_end` | A tool call finished |
| `agent_end` | The engine loop ended; post-run processing may remain |
| `settled` | The invocation finished post-run work with nothing queued |
| `session_shutdown` | The active session is being replaced |
| `runtime_stop` | `ashutdown()` was called |

Wait on `settled` (not `agent_end`) when you need the turn to be completely finished, including compaction.

## Options Reference

### RuntimeConfig Fields

#### Directories and Session

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cwd` | `Path` | *required* | Working directory for the session; resolved on use |
| `config_dir` | `Path \| None` | `None` | Override the config directory (default `~/.tau`) |
| `session_file` | `Path \| None` | `None` | Open or resume this specific session file |
| `session_dir` | `Path \| None` | `None` | Session storage root; falls back to the `session_dir` setting, then the per-project default |
| `persist_session` | `bool` | `True` | Write the session to disk; `False` is ephemeral |
| `resume` | `bool` | `False` | Resume the most recent session for `cwd` |

#### Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model_id` | `str \| None` | `None` | Model ID; falls back to settings, then `"claude-sonnet-4-6"` |
| `provider` | `str \| None` | `None` | Provider ID; falls back to settings, then `"anthropic"` |
| `base_url` | `str \| None` | `None` | Per-run base-URL override for the resolved provider; not persisted |
| `thinking_level` | `str \| None` | `None` | Per-run thinking-level override, clamped to the model; not persisted |

#### Startup Conversation Seed

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `initial_messages` | `list[AgentMessage]` | `[]` | History appended to the session before `session_start` |
| `initial_prompt` | `str \| None` | `None` | Text of a startup user message |
| `initial_images` | `list[Any]` | `[]` | Images attached to the startup user message |
| `initial_audio` | `list[str \| bytes]` | `[]` | Audio attached to the startup user message |
| `initial_video` | `list[str \| bytes]` | `[]` | Video attached to the startup user message |

Supplying any initial media creates a user message even when `initial_prompt` is omitted. The seed is consumed once by `Runtime.create()` and is *not* replayed when that runtime later creates or resumes another session.

#### Tools and Prompt

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `str` | `"interactive"` | `"interactive"`, `"print"`, `"json"`, or `"rpc"`. For the `"rpc"` protocol, see [RPC Mode](rpc.md) |
| `tools` | `list[Tool]` | `[]` | Extra tools registered under the `"runtime"` source |
| `tool_allowlist` | `set[str] \| None` | `None` | Enable only these tool names; `None` enables all |
| `exclude_tools` | `set[str]` | `set()` | Tool names disabled after the allowlist is applied |
| `system_prompt` | `str` | `""` | Complete system-prompt replacement; skips the generated tool, context, skill, git, and append sections |
| `disable_context_files` | `bool` | `False` | Skip `AGENTS.md` / `CLAUDE.md` discovery |
| `resource_loader` | `ResourceLoader \| None` | `None` | Replace resource discovery and registry loading |
| `extension_factories` | `list[ExtensionFactory]` | `[]` | In-memory extensions loaded at startup and on reload |
| `dependencies` | `RuntimeDependencies` | `RuntimeDependencies()` | Factories for injected services |
| `project_trusted` | `bool \| None` | `None` | Override trust detection; `None` uses the trust store and policy |

Untrusted projects do not load context files and do not create the session directory until trust is granted.

### RuntimeDependencies

`RuntimeDependencies` (`tau.runtime.dependencies`) is a frozen dataclass of optional factories. Each receives a frozen context dataclass and returns the service.

| Factory | Context | Returns | Purpose |
|---------|---------|---------|---------|
| `settings` | `SettingsFactoryContext` | `SettingsManager` | Settings access |
| `llm` | `LLMFactoryContext` | `TextLLM` | The text LLM, including custom model/provider/API/auth registries |
| `session_manager` | `SessionManagerFactoryContext` | `SessionManager` | Persistent, in-memory, or custom session storage |
| `hooks` | *(no argument)* | `Hooks` | The shared lifecycle event bus |
| `tool_registry` | *(no argument)* | `ToolRegistry` | The tool registry |

| Context | Fields |
|---------|--------|
| `SettingsFactoryContext` | `cwd`, `config_dir`, `project_trusted` |
| `LLMFactoryContext` | `model_id`, `provider`, `settings` |
| `SessionManagerFactoryContext` | `cwd`, `session_dir`, `session_file`, `persist`, `resume` |

```python
from tau.hooks.service import Hooks
from tau.inference.api.text.service import TextLLM
from tau.runtime.dependencies import LLMFactoryContext, RuntimeDependencies
from tau.session.manager import SessionManager

shared_hooks = Hooks()


def create_llm(context: LLMFactoryContext) -> TextLLM:
    return TextLLM(model_id=context.model_id, provider=context.provider)


config = RuntimeConfig(
    cwd=Path.cwd(),
    dependencies=RuntimeDependencies(
        llm=create_llm,
        hooks=lambda: shared_hooks,
        session_manager=lambda ctx: SessionManager.in_memory(ctx.cwd),
    ),
)
```

The LLM, session-manager, and tool-registry factories run again whenever Tau replaces the active session. The settings manager and hook bus are preserved across replacements. The LLM factory is also used by `set_model()`.

## Runtime API

### Factories

| Method | Returns | Description |
|--------|---------|-------------|
| `Runtime.create(config)` | `Runtime` | Build the runtime and emit `session_start` + `runtime_ready` |
| `Runtime.create_with_result(config)` | `RuntimeStartupResult` | Same, plus structured startup diagnostics |

### Prompting

| Method | Description |
|--------|-------------|
| `invoke(text, options=None, *, display=False)` | Send a plain prompt to the agent. `display=True` echoes it into an attached TUI transcript |
| `user_input(text, options=None)` | Full input router: `!cmd` shells out, `!!cmd` shells out privately, `/name` dispatches a command, `/skill:name` expands a skill, anything else calls `invoke()` |
| `steer(message)` | Queue a message for the active turn, delivered after its current tool round |
| `follow_up(message)` | Queue a message for delivery after the active turn finishes |
| `execute_terminal(cmd, exclude=False)` | Run a shell command, stream output, and persist it to the session |

`options` is a `PromptOptions` (`tau.agent.types`) carrying attachments:

| Field | Type | Default |
|-------|------|---------|
| `meta` | `MessageMeta \| None` | `None` |
| `images` | `list[bytes]` | `[]` |
| `audio` | `list[bytes]` | `[]` |
| `video` | `list[bytes]` | `[]` |
| `file` | `list[bytes]` | `[]` |

`invoke()` raises `RuntimeError("No active session available.")` when there is no agent.

### Session Lifecycle

| Method | Description |
|--------|-------------|
| `new_session(*, with_session=None)` | Shut down the current session and start an empty one |
| `resume_session(path, *, with_session=None)` | Shut down and reopen an existing session file |
| `fork_session(entry_id, *, position="at", with_session=None)` | Branch the tree at an entry and continue in the same file |
| `clone_session()` | Copy the current branch into a new session file and switch to it |
| `navigate_tree(target_id, *, summarize=False, custom_instructions=None, replace_instructions=False, label=None)` | Move the leaf to another entry, optionally summarizing the abandoned branch. Returns `False` if an extension cancelled it |

`fork_session()` and `navigate_tree()` raise `KeyError` for an unknown entry ID. `clone_session()` raises `ValueError` when there is no active leaf. The optional `with_session` callback receives a fresh `ExtensionContext` after the swap.

See [Sessions](sessions.md) for the storage format and the `SessionManager` API.

### Model and Extensions

| Method | Description |
|--------|-------------|
| `set_model(model_id, provider=None)` | Swap the active model. Returns `False` if there is no agent or the model could not be built. Only safe while idle |
| `reload_extensions()` | Re-discover and reload all extensions, skills, prompts, and settings |
| `reload_extension(ext_path)` | Reload a single extension by module path, leaving the others untouched |

Reload calls made during an extension callback or an active agent turn are deferred and coalesced until a safe boundary. Both rebuild the system prompt and sync tools into the live engine, no new session needed.

`set_model()` marks prior thought signatures as untrusted for the rest of the session, closes the outgoing provider's HTTP client, records a `model_change` session entry, and persists the new `text` model reference.

### Observation and Shutdown

| Method | Description |
|--------|-------------|
| `subscribe(listener)` | Receive every runtime event; returns an unsubscribe callable |
| `notify(message)` | Post a system status note to the attached TUI (no-op without one) |
| `set_layout(layout)` | Attach a TUI layout |
| `set_extension_ui_refresh(callback)` | Register the interactive-mode extension UI refresh callback |
| `ashutdown()` | Async teardown: cancel background tasks, emit `runtime_stop`, unsubscribe extensions. Idempotent |
| `shutdown()` | No-op retained for API compatibility; use `ashutdown()` |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `agent` | `Agent \| None` | The active agent |
| `hooks` | `Hooks` | Shared event bus |
| `session_manager` | `SessionManager` | Active session storage |
| `settings_manager` | `SettingsManager \| None` | Settings access |
| `extension_runtime` | `ExtensionRuntime \| None` | Loaded extensions |
| `extension_generation` | `int` | Bumped on every lifecycle replacement; rejects stale contexts |
| `extension_shortcuts` | `list` | Keyboard shortcuts registered by extensions |
| `resource_diagnostics` | `tuple[ResourceDiagnostic, ...]` | Diagnostics from the latest discovery |
| `commands` | `CommandRegistry` | Registered slash commands |

### Agent

`runtime.agent` is the lower-level session agent (`tau.agent.service.Agent`).

```python
agent = runtime.agent

agent.phase                  # AgentPhase.IDLE | TURN | COMPACTION | BRANCH_SUMMARY
agent.is_idle()              # bool
agent.has_pending_messages() # bool
agent.streaming_message      # AssistantMessage | None
agent.pending_tool_call_ids  # frozenset[str]
agent.error_message          # str | None
agent.queued_messages        # dict[str, list[LLMMessage]]
agent.cwd                    # Path
agent.session_manager        # SessionManager

agent.get_context_usage()    # ContextUsage(tokens, context_window, percent) | None
agent.get_system_prompt()    # str

await agent.wait_for_idle()  # through save-point handlers and post-run compaction
await agent.compact(custom_instructions=None)  # -> bool
agent.abort()
```

### ToolRegistry

`runtime._context.tool_registry` is the single source of truth for registered tools, tracked by source (`"builtin"`, `"runtime"`, `"extension"`).

```python
registry = runtime._context.tool_registry

registry.list()                     # all tools
registry.list(source="extension")   # tools from extensions
registry.names()                    # set[str]
registry.sources()                  # set[str]
registry.get("read")                # Tool | None
"read" in registry                  # bool
len(registry)                       # int

registry.register(MyTool(), source="runtime")
registry.unregister("my_tool")
registry.replace_source("runtime", [MyTool()])
registry.sync_to_engine(runtime.agent._engine)
```

Mutations only reach the model after `sync_to_engine()`.

## Custom Tools

Pass tools in `RuntimeConfig.tools` to register them under the `"runtime"` source at startup.

```python
from pydantic import BaseModel, Field

from tau.tool.types import Tool, ToolKind, ToolResult


class WordCountSchema(BaseModel):
    path: str = Field(..., description="Path to the file to count words in")


class WordCountTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="word_count",
            description="Count the words in a text file.",
            schema=WordCountSchema,
            kind=ToolKind.Read,
        )

    async def execute(
        self,
        invocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ) -> ToolResult:
        try:
            text = Path(invocation.params["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.error(invocation.id, str(exc))
        return ToolResult.ok(invocation.id, f"{len(text.split())} words")


config = RuntimeConfig(cwd=Path.cwd(), tools=[WordCountTool()])
```

`ToolKind` is one of `Read`, `Edit`, `Write`, `Execute`, `Web`. The engine applies its execution and approval policy from it. See [Creating Tools](creating-tools.md) for rendering, approval, and streaming details.

## Inline Extensions

Register extensions without creating files by passing factories in `extension_factories`.

```python
from tau.extensions import ExtensionAPI


def configure(tau: ExtensionAPI) -> None:
    tau.register_tool(WordCountTool())
    tau.append_prompt("Follow the host application's conventions.")

    @tau.on("agent_end")
    async def observe(event, context) -> None:
        print("turn finished in session", context.session_id)


config = RuntimeConfig(cwd=Path.cwd(), extension_factories=[configure])
```

Factories may be sync or async. They run *after* file-based extensions, so their registrations win on name collisions. A failing factory is recorded as a normal `ExtensionError` and does not block the others. `reload_extensions()` runs every factory again.

See [Extensions](extensions.md) for the full `ExtensionAPI`.

## Custom Resource Loaders

`RuntimeConfig.resource_loader` accepts any object implementing the `ResourceLoader` protocol: `discover()`, `create_extension_loader()`, and `apply_registries()`. Tau passes a `ResourceContext` (`cwd`, `settings`, `hooks`, `load_context_files`) on startup and on every reload, and keeps the same loader instance throughout.

`DefaultResourceLoader` supports focused overrides without subclassing. Each callback receives the discovered tuple and returns a replacement.

| Callback | Signature |
|----------|-----------|
| `extensions_override` | `(tuple[ExtensionEntry, ...]) -> tuple[ExtensionEntry, ...]` |
| `skills_override` | `(tuple[Path, ...]) -> tuple[Path, ...]` |
| `prompts_override` | `(tuple[Path, ...]) -> tuple[Path, ...]` |
| `themes_override` | `(tuple[Path, ...]) -> tuple[Path, ...]` |
| `context_files_override` | `(tuple[ContextFile, ...]) -> tuple[ContextFile, ...]` |
| `system_prompt_override` | `() -> str \| None` |

```python
from tau.resources import DefaultResourceLoader

loader = DefaultResourceLoader(
    skills_override=lambda current: (*current, Path("shared-skills")),
    system_prompt_override=lambda: "Use the project engineering standards.",
)
config = RuntimeConfig(cwd=Path.cwd(), resource_loader=loader)
```

Subclass instead when you need to reshape the whole snapshot:

```python
from dataclasses import replace

from tau.resources import DefaultResourceLoader, ResourceContext, ResourceSnapshot


class ProjectResourceLoader(DefaultResourceLoader):
    async def discover(self, context: ResourceContext) -> ResourceSnapshot:
        snapshot = await super().discover(context)
        return replace(
            snapshot,
            skill_paths=(*snapshot.skill_paths, context.cwd / "agent-skills"),
        )
```

`ResourceSnapshot` is a dataclass with `builtins_extension_dir`, `project_extension_dir`, `global_extension_dir`, `extension_entries`, `extension_sources`, `disabled_extension_stems`, `extension_configs`, `skill_paths`, `prompt_paths`, `theme_paths`, `context_files`, `system_prompt`, and `diagnostics`.

`DefaultResourceLoader` reports diagnostics for missing or invalid configured extension paths, missing installed-package directories, malformed package manifests, package selectors that match nothing, missing hook-contributed or override paths, and unreadable context files. Each `ResourceDiagnostic` carries `"warning"` or `"error"` severity, a source, a message, and an optional path. Diagnostics never stop startup; loading continues with the valid resources. Read the latest set from `runtime.resource_diagnostics`.

## Ephemeral Context Injection

Browser and computer-use agents can inject fresh state before every model request without persisting it, by configuring the engine directly:

```python
from tau.engine import Engine, EngineOptions
from tau.message.types import UserMessage


async def current_browser_state() -> list[UserMessage]:
    return [UserMessage.with_images("Current browser state", images=[screenshot])]


engine = Engine(
    cwd=Path.cwd(),
    llm=llm,
    tools=tools,
    options=EngineOptions(ephemeral_injection=current_browser_state),
)
```

The callback runs after context transformation and before each inference, including inference after tool execution. Failures are logged and ignored. Injected messages are appended only to the request copy, and Anthropic prompt-cache breakpoints exclude this transient tail. See [Engine](engine.md).

## Complete Example

A runnable batch reviewer: a custom tool, an inline extension, injected dependencies, event capture, and a fresh session per file.

```python
import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from tau.extensions import ExtensionAPI
from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage
from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig
from tau.tool.types import Tool, ToolKind, ToolResult


class WordCountSchema(BaseModel):
    path: str = Field(..., description="Path to the file to count words in")


class WordCountTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="word_count",
            description="Count the words in a text file.",
            schema=WordCountSchema,
            kind=ToolKind.Read,
        )

    async def execute(
        self,
        invocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ) -> ToolResult:
        try:
            text = Path(invocation.params["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.error(invocation.id, str(exc))
        return ToolResult.ok(invocation.id, f"{len(text.split())} words")


def configure(tau: ExtensionAPI) -> None:
    tau.append_prompt("Be terse. Report only real defects, with file:line references.")


async def review(files: list[str]) -> dict[str, str]:
    config = RuntimeConfig(
        cwd=Path.cwd(),
        persist_session=False,
        mode="print",
        tools=[WordCountTool()],
        tool_allowlist={"read", "grep", "word_count"},
        extension_factories=[configure],
        dependencies=RuntimeDependencies(hooks=lambda: Hooks()),
    )

    result = await Runtime.create_with_result(config)
    runtime = result.runtime
    for error in result.extension_errors:
        print("extension failed:", error.extension_path, error.error)

    reviews: dict[str, str] = {}
    try:
        for path in files:
            chunks: list[str] = []
            settled = asyncio.Event()

            async def on_message_end(event) -> None:
                message = getattr(event, "message", None)
                if isinstance(message, AssistantMessage):
                    for content in message.contents:
                        text = getattr(content, "content", None)
                        if isinstance(text, str):
                            chunks.append(text)

            async def on_settled(event) -> None:
                settled.set()

            unsub_message = runtime.hooks.register("message_end", on_message_end)
            unsub_settled = runtime.hooks.register("settled", on_settled)

            await runtime.invoke(f"Review {path} for bugs.")
            await settled.wait()

            unsub_message()
            unsub_settled()
            reviews[path] = "".join(chunks)

            await runtime.new_session()
    finally:
        await runtime.ashutdown()

    return reviews


if __name__ == "__main__":
    for path, text in asyncio.run(review(["tau/runtime/service.py"])).items():
        print(f"=== {path} ===\n{text}\n")
```

## Exports

Tau has **no top-level namespace exports**. `import tau` gives you nothing but the package. Import from the specific module.

| Import | Provides |
|--------|----------|
| `tau.runtime.service` | `Runtime` |
| `tau.runtime.types` | `RuntimeConfig`, `RuntimeContext`, `RuntimeStartupResult` |
| `tau.runtime.dependencies` | `RuntimeDependencies`, `SettingsFactoryContext`, `LLMFactoryContext`, `SessionManagerFactoryContext` |
| `tau.agent.service` | `Agent` |
| `tau.agent.types` | `AgentConfig`, `AgentPhase`, `PromptOptions`, `ContextUsage` |
| `tau.engine` | `Engine`, `EngineOptions`, `EngineState`, `EngineContext`, and the `Agent*`/`Message*`/`ToolExecution*` event types |
| `tau.session.manager` | `SessionManager` |
| `tau.session.types` | `SessionHeader`, `MessageEntry`, `SessionContext`, `SessionInfo`, `SessionTreeNode`, and the other entry models |
| `tau.settings.manager` | `SettingsManager` |
| `tau.hooks.service` | `Hooks`, `Handler`, `Unsubscribe` |
| `tau.inference.api.text.service` | `TextLLM` |
| `tau.message.types` | `AgentMessage`, `UserMessage`, `AssistantMessage`, `ToolMessage`, `CustomMessage`, content types |
| `tau.tool.types` | `Tool`, `ToolKind`, `ToolResult`, `ToolInvocation`, `ToolExecutionMode` |
| `tau.tool.registry` | `ToolRegistry` |
| `tau.resources` | `DefaultResourceLoader`, `ResourceLoader`, `ResourceContext`, `ResourceSnapshot`, `ResourceDiagnostic`, `ContextFile` |
| `tau.extensions` | `ExtensionAPI`, `Extension`, `ExtensionError`, `ExtensionRuntime`, `ExtensionContext`, `ExtensionLoader`, `LoadExtensionsResult` |
| `tau.commands.registry` | `CommandRegistry` |
| `tau.commands.types` | `CommandInfo`, `ParsedCommand` |
| `tau.settings.paths` | `get_config_dir`, `get_sessions_dir`, `get_logs_dir`, `get_app_version`, and the other path helpers |

`tau.session` and `tau.runtime` have no `__init__.py` re-exports; always import the submodule.

## Next Steps

- [Engine](engine.md): the streaming and tool-execution loop on its own
- [Sessions](sessions.md): session storage, format, and the `SessionManager` API
- [Extensions](extensions.md): the full extension API
- [Creating Tools](creating-tools.md): tool rendering, approval, and streaming
- [Settings](settings.md): configuration reference
