# Architecture

Tau is a layered system. Each layer depends only on the layers beneath it, and
the three lowest layers (`tau.tui`, `tau.engine`, `tau.inference`) are usable
on their own. This page describes the layering, the data flow through one turn,
and the boundaries that keep the layers separable.

For the file-by-file inventory, see [Project Structure](project-structure.md).

## Table of Contents

- [Layers](#layers)
- [Layer Responsibilities](#layer-responsibilities)
- [Dependency Rules](#dependency-rules)
- [Turn Data Flow](#turn-data-flow)
- [Agent Phases](#agent-phases)
- [Context Building](#context-building)
- [Tool Execution](#tool-execution)
- [Hooks and Events](#hooks-and-events)
- [Resource Loading](#resource-loading)
- [Runtime Composition](#runtime-composition)
- [Provider Abstraction](#provider-abstraction)
- [Security Boundaries](#security-boundaries)

## Layers

```text
┌───────────────────────────────────────────────────────────────────┐
│ console/                       CLI entry, mode resolution         │
│   tau … → interactive | print | json | rpc                        │
└───────────────────────────────┬───────────────────────────────────┘
                                │ constructs
┌───────────────────────────────▼───────────────────────────────────┐
│ modes/                         One driver per run mode            │
│   interactive/  App, input handler, agent_hooks, components       │
│   rpc/          JSON-RPC over stdio                               │
│   (print/json run from console/cli.py)                            │
└───────────────────────────────┬───────────────────────────────────┘
                                │ drives
┌───────────────────────────────▼───────────────────────────────────┐
│ runtime/                       Application wiring                 │
│   Runtime.create() → sessions, extensions, tools, hooks, model    │
│   user_input · steer · follow_up · set_model · navigate_tree      │
└──┬──────────────┬──────────────┬──────────────┬───────────────────┘
   │              │              │              │
   │ resources/   │ extensions/  │ session/     │ owns an
   │ trust/       │ commands/    │ settings/    │
   │ packages/    │ hooks/       │ auth/        │
   │              │              │              ▼
   │              │              │  ┌────────────────────────────────┐
   │              │              │  │ agent/     One session-aware   │
   │              │              └──┤            turn                │
   │              │                 │ invoke · compact · persist ·   │
   │              │                 │ retry · context building       │
   │              │                 └──────────────┬─────────────────┘
   │              │                                │ owns an
   │              │                 ┌──────────────▼─────────────────┐
   │              └────hooks────────┤ engine/    Stream + tool loop  │
   │                                │ run · run_continue · steer ·   │
   │                                │ tool dispatch · abort          │
   │                                └──────┬───────────────┬─────────┘
   │                                       │               │
   │                          ┌────────────▼────┐  ┌───────▼────────┐
   │                          │ inference/      │  │ tool/          │
   │                          │ TextLLM, APIs,  │  │ Tool ABC,      │
   │                          │ providers,      │  │ ToolRegistry   │
   │                          │ models, OAuth   │  │ builtins/tools │
   │                          └─────────────────┘  └────────────────┘
   │
   │  ┌──────────────────────────────────────────────────────────────┐
   └──┤ message/  Shared vocabulary — every layer above speaks these  │
      └──────────────────────────────────────────────────────────────┘

      ┌──────────────────────────────────────────────────────────────┐
      │ tui/      Standalone terminal framework. Knows nothing about  │
      │           agents. Consumed only by modes/interactive/.        │
      └──────────────────────────────────────────────────────────────┘
```

## Layer Responsibilities

| Layer | Owns | Explicitly does not own |
|-------|------|-------------------------|
| `console/` | Argument parsing, mode resolution, print/json runners | Anything about turns |
| `modes/` | Per-mode drivers and Tau-specific UI composition | Turn mechanics, persistence |
| `runtime/` | Wiring, session lifecycle, extension reload, model switching | Streaming, tool dispatch |
| `agent/` | Context building, compaction, persistence, retry, phase tracking | Streaming, tool dispatch |
| `engine/` | Streaming, tool validation and dispatch, steering/follow-up queues | Sessions, extensions, UI |
| `inference/` | Auth, endpoints, request shaping, normalized event stream | Tool execution, history |
| `tool/` | `Tool` contract and registry | Scheduling, that is the engine's |
| `message/` | Message and content-block vocabulary | Persistence, transport |
| `tui/` | Terminal rendering, components, input, keybindings | Agents, sessions, models |

## Dependency Rules

Three packages are hard boundaries and are enforced as such:

| Package | May import | Must not import |
|---------|-----------|-----------------|
| `tau.tui` | Standard library, rendering deps | Any other `tau.*` application package |
| `tau.engine` | `inference`, `message`, `tool`, `hooks`, optionally `settings` | `session`, `extensions`, `tui`, `runtime`, `agent` |
| `tau.inference` | `message`, `auth`, `settings`, `utils` | `engine`, `agent`, `session`, `runtime`, `tui` |

This is what makes each of them independently testable and embeddable. See
[Engine](engine.md#dependency-boundary), [Inference](inference.md), and
[Terminal UI](tui.md) for the standalone usage of each.

`agent/` imports `engine/` (it constructs and owns one). `engine/` never imports
`agent/`. Communication upward happens through events on the shared `Hooks` bus
and through the `EngineOptions` callbacks the agent installs.

## Turn Data Flow

One interactive turn, end to end:

```text
 1. modes/interactive/input_handler.py   user submits text
 2. Runtime.user_input()                 emit InputEvent (extensions may rewrite)
 3. Agent.invoke()                       phase IDLE → TURN
 4. SessionManager                       append UserMessage as a MessageEntry
 5. session.utils.to_llm_messages()      AgentMessage[] → LLMMessage[]
 6. agent/prompt/builder.py              build system prompt + project context
 7. hooks: BeforeAgentStartEvent         handlers may override the system prompt
 8. Agent → Engine.run(EngineContext)    hand off system prompt, messages, tools
 9. EngineOptions.transform_context      last rewrite before the request
10. hooks: ContextEvent                  handlers may rewrite the message list
11. inference: TextLLM.stream()          provider request; normalized LLMEvent stream
12. engine: MessageStart/Update/End      AssistantMessage accumulates and commits
13. assistant.tool_calls()               engine schedules the batch
14. tool/: Tool.execute()                results become ToolResultContent
15. engine: ToolExecutionStart/End       per call
16. engine: TurnEndEvent                 assistant message + all tool results
17. loop to step 11 if tools ran         otherwise AgentEndEvent
18. Agent                                persist messages, update token usage
19. modes/interactive/agent_hooks.py     project events onto UI state; render
20. Agent                                post-turn compaction check, drain queues
21. hooks: SettledEvent                  phase TURN → IDLE
```

Steps 11–17 are entirely inside `tau.engine` and run identically when the engine
is embedded without a runtime.

## Agent Phases

`AgentPhase` in `agent/types.py` is a `StrEnum` with four members.

```text
IDLE ──user input──────────► TURN ──no more tool calls──► IDLE
  ├── manual or auto compact ──► COMPACTION ──► previous phase
  └── tree navigation ─────────► BRANCH_SUMMARY ──► IDLE
```

| Phase | Value | Meaning |
|-------|-------|---------|
| `IDLE` | `idle` | No active inference; waiting for input |
| `TURN` | `turn` | Calling inference and executing tools |
| `COMPACTION` | `compaction` | Generating or applying a context compaction summary |
| `BRANCH_SUMMARY` | `branch_summary` | Generating or applying a branch-navigation summary |

Read the current phase with `Agent.phase`, or `Agent.is_idle` for the common
case. Slash commands declare whether they require an idle agent: idle-only
commands are deferred until the turn settles, while UI-only and read-only
commands may opt into immediate dispatch mid-turn.

## Context Building

Before each request the agent assembles context in this order:

1. Load the active branch from the session tree (JSONL on disk).
2. Apply compaction: replace summarized history with a `CompactionSummaryMessage`.
3. Project `AgentMessage[]` to `LLMMessage[]` via `session.utils.to_llm_messages()`.
4. Build the system prompt: environment detection, git status, project context files.
5. Inject skills and expanded prompt templates.
6. Apply `EngineOptions.ephemeral_injection` for single-turn messages.
7. Estimate token usage against the model's context window.

Projection rules (which persisted messages reach the model and how) are in
[Messages](messages.md#context-projection).

Compaction is checked at three points:

| Trigger | When | Reason value |
|---------|------|--------------|
| Pre-flight | Before sending a turn | `threshold` |
| Post-turn | After a turn completes | `threshold` |
| Reactive | A request fails with a provider context-overflow error | `overflow` |
| Explicit | `/compact` | `manual` |

The reactive path is a backstop: it compacts and retries once, bounded to a
single attempt. See [Context Compaction](sessions.md#context-compaction).

## Tool Execution

```text
ToolCallContent from the model
   │
   ├─ hooks: ToolCallEvent            handlers may block or rewrite params
   ├─ EngineOptions.should_skip_tool_calls   returns a result without executing
   ├─ EngineOptions.before_tool_call  rewrite the invocation, or short-circuit
   ├─ trust/ check                    for tools requiring project trust
   │
   ├─ schedule by ToolExecutionMode   Sequential | Parallel | Batch
   │    └─ timeout + abort boundary   EngineOptions.tool_timeout_seconds
   │
   ├─ Tool.execute(invocation)        may stream ToolExecutionUpdateEvent
   ├─ EngineOptions.after_tool_call   inspect or replace the result
   ├─ hooks: ToolResultEvent          handlers may override the content
   │
   ▼
ToolResultContent (id matches the call) → ToolMessage → next turn
```

Batch scheduling is all-or-nothing: a batch runs concurrently only when every
tool in it declares `Parallel`. One sequential tool is an ordering barrier for
the whole batch, because parallel tools running around it could reorder
observable side effects. Results are always returned in source order.

Failures are contained per call. A tool that raises produces an error result for
that call only; siblings continue. Full detail in
[Engine](engine.md#tool-execution).

Built-in tools (`read`, `write`, `edit`, `glob`, `grep`, `ls`, `terminal`) are
enabled by default. `RuntimeConfig` narrows that set with `tool_allowlist`
(`set[str] | None`) and `exclude_tools` (`set[str]`), and accepts extra tools
directly via its `tools` field. Custom tools are also registered by extensions, see
[Creating Tools](creating-tools.md).

## Hooks and Events

`tau/hooks/` is one bus, `Hooks`, with events split across domain modules. The
`HookEvent` union in `hooks/types.py` aggregates all of them.

| Module | Domain | Representative events |
|--------|--------|----------------------|
| `hooks/engine.py` | Turn lifecycle | `before_agent_start`, `agent_start`, `agent_end`, `agent_error`, `turn_start`, `turn_end`, `message_start`, `message_update`, `message_end`, `message_rollback`, `context`, `tool_call`, `tool_result`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end`, `tool_execution_failure`, `save_point`, `settled`, `before_compaction`, `compaction_start`, `compaction_end`, `compaction_failure`, `compaction_cancelled` |
| `hooks/session.py` | Session lifecycle | `session_start`, `session_before_switch`, `session_before_fork`, `session_before_tree`, `session_tree`, `session_shutdown`, `branch_summary_start`, `branch_summary_end`, `branch_summary_failure`, `branch_summary_cancelled` |
| `hooks/runtime.py` | Application lifecycle | `runtime_start`, `runtime_ready`, `runtime_stop`, `input`, `project_trust`, `resources_discover`, `terminal_execution`, `terminal_output`, `user_terminal` |
| `hooks/inference.py` | Provider boundary | `before_provider_request`, `after_provider_response` |
| `hooks/tui.py` | Interface | `tui_start`, `tui_ready`, `tui_exit`, `queue_update`, `model_select`, `thinking_level_select` |

Two kinds of handler exist. **Observational** handlers just react.
**Mutating** handlers return a typed result object (`ContextEventResult`,
`ToolCallEventResult`, `ToolResultEventResult`, `MessageEndEventResult`,
`BeforeCompactionResult`) which the emitter reduces into the ongoing operation.

```python
def register(tau):
    async def on_tool_end(event, ctx):
        print(f"{event.tool_result.tool_name} finished")

    tau.on("tool_execution_end", on_tool_end)
```

`Hooks.emit()` accepts a per-emit timeout. Handlers that exceed it are logged
and abandoned so a slow extension cannot stall the turn. The complete event
table is in [Extensions](extensions.md#event-hooks).

## Resource Loading

Resource discovery sits behind the replaceable `ResourceLoader` protocol in
`resources/loader.py`. `DefaultResourceLoader` supports per-resource overrides.

Startup and `/reload` each consume exactly one immutable `ResourceSnapshot`,
which keeps extensions, skills, prompts, themes, and context files consistent
with each other. Sources merge in priority order:

```text
builtin  →  global (~/.tau/)  →  project (.tau/)  →  installed packages
                                                  →  hook-provided
```

Structured diagnostics report invalid configured paths, bad package manifests,
unreadable hook paths, and context-file read failures without preventing valid
resources from loading. Read them from
`Runtime.resource_diagnostics` or `RuntimeStartupResult`.

## Runtime Composition

`Runtime` has two constructors:

| Constructor | Returns | Use when |
|-------------|---------|----------|
| `Runtime.create(config)` | `Runtime` | Startup details are not needed |
| `Runtime.create_with_result(config)` | `RuntimeStartupResult` | You need diagnostics, extension errors, and requested-versus-selected model/provider resolution |

`RuntimeDependencies` replaces constructed services with typed factories,
covering settings, LLM/model/auth wiring, session storage, hooks, and the tool
registry. Session-bound factories run again whenever the active session is
replaced.

Extension reloads pass through a serialized coordinator: callback-triggered or
mid-turn requests wait until extension dispatch and the agent lifecycle settle.
Runtime generations invalidate contexts captured before a reload, session
replacement, or shutdown, and shutdown detaches the extension runtime from the
hook bus.

See [Python API](python-api.md) for programmatic embedding.

## Provider Abstraction

Providers describe authentication, endpoints, and the API adapter used for a
request. Adapters stream normalized `LLMEvent` objects, so the engine is written
once against one event shape.

```python
class BaseAPI:
    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:
        """Yield LLMEvent objects as the provider streams its response."""

    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]:
        """Collect the full response without streaming."""
```

| Concern | Owner |
|---------|-------|
| Wire format per API | `inference/api/<modality>/*.py` |
| Which provider serves a model | `inference/provider/registry.py` |
| Model metadata, limits, cost | `inference/model/registry.py`, `builtins/models/` |
| Dynamic model discovery | `inference/model/catalog.py` (models.dev) |
| Local model discovery | `inference/model/local/` (llama.cpp, LM Studio, Ollama, vLLM) |
| Credentials | `auth/manager.py` and `inference/provider/oauth/` |
| Failure classification | `inference/utils.py` → `ErrorKind` |

`ErrorKind` drives recovery: `rate_limit` and `overloaded` back off and retry,
`billing` and `auth_permanent` abort immediately, and a context-overflow
classification triggers compact-and-retry.

Text, image, audio, and video are separate modalities with their own registries
and client services. See [Inference](inference.md) and
[Inference Providers](inference-providers.md).

## Security Boundaries

| Boundary | Mechanism |
|----------|-----------|
| Project trust | `trust/manager.py` persists per-directory decisions to `~/.tau/trust.json`. Context files, extensions, and project settings load only for trusted projects. |
| Credentials | Resolved by `auth/manager.py` from environment variables or the credential store; `utils/secrets.py` resolves `$VAR` references in settings. |
| Package resolution | `packages/manager.py` rejects path traversal and symlink escape when resolving a package declaration. |
| Tool execution | Tools declare a `ToolKind`; the trust system gates the ones that write or execute. |
| Session logs | One log file per run under the global logs directory, named by session id. |

> **Security:** Extensions execute arbitrary Python in-process, and skills can
> instruct the model to take any action. Review third-party code before
> installing it.

## Design Principles

1. **Strict layering**: each layer depends only downward; upward communication is via events.
2. **Standalone cores**: `tui`, `engine`, and `inference` are independently usable.
3. **One vocabulary**: every layer speaks `tau.message` types.
4. **Replaceable seams**: `ResourceLoader`, `RuntimeDependencies`, and the hook bus are the extension points.
5. **Contained failure**: a failing tool, handler, or resource degrades one unit of work, not the turn.
6. **Explicit phase**: `AgentPhase` gates structural operations rather than ad-hoc busy flags.

## Next Steps

- [Project Structure](project-structure.md) - module-by-module inventory
- [Engine](engine.md) - the embeddable loop
- [Messages](messages.md) - the shared type vocabulary
- [Extensions](extensions.md) - building on the hook bus
- [Python API](python-api.md) - programmatic runtime usage
