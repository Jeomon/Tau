# Architecture

This page explains the design and structure of tau at a high level.

## Core Components

Tau is built around a modular architecture with clear separation of concerns:

```text
┌─────────────────────────────────────────────────────┐
│           Console (CLI Entry Point)                 │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│           TUI (Terminal User Interface)             │
│  ┌────────────┬──────────────┬──────────┬──────────┐
│  │ Renderer   │ Input Editor │ Theme    │ Bindings │
│  └────────────┴──────────────┴──────────┴──────────┘
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│      Runtime (Agent Execution Engine)              │
│  ┌────────────┬──────────────┬──────────┬──────────┐
│  │ Agent      │ Messages     │ Sessions │ Hooks    │
│  └────────────┴──────────────┴──────────┴──────────┘
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│         Engine (Tool Execution)                    │
│  ┌────────────┬──────────────┬──────────┬──────────┐
│  │ Builtins   │ Extensions   │ Commands │ Skills   │
│  └────────────┴──────────────┴──────────┴──────────┘
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│    Inference (LLM Provider Abstraction)            │
│  ┌────────────┬──────────────┬──────────┬──────────┐
│  │ Anthropic  │ OpenAI       │ Google   │ Others   │
│  └────────────┴──────────────┴──────────┴──────────┘
└──────────────────────────────────────────────────────┘
```

## Data Flow

User input flows through these stages:

1. **Input** - User types a prompt in the TUI editor
2. **Session** - Message added to the current session
3. **Agent** - Agent processes message and tool outputs
4. **Inference** - Agent calls the LLM provider
5. **Tools** - LLM requests tools; engine executes them
6. **Render** - Results rendered in the TUI
7. **Save** - Session and context updated and persisted

## Module Organization

### Core Modules (Request/Response)

| Module | Purpose |
|--------|---------|
| `console/` | CLI entry point, argument parsing |
| `runtime/` | Orchestrates agent, session, engine, extensions |
| `agent/` | Processes message turns, calls inference |
| `engine/` | Executes tool calls and collects results |
| `inference/` | Unified interface to LLM providers |

### Data & State

| Module | Purpose |
|--------|---------|
| `session/` | Session JSONL persistence, compaction, branching |
| `settings/` | Configuration from JSON files |
| `auth/` | Credential storage and resolution |
| `message/` | Message type definitions |
| `trust/` | Trust and permission checks |

### UI & Extensions

| Module | Purpose |
|--------|---------|
| `tui/` | Terminal UI (22 modules) |
| `extensions/` | Plugin system API |
| `hooks/` | Event system for extensions |
| `commands/` | Slash command registry |
| `tool/` | Tool registry and abstractions |

### Built-in Content

| Module | Purpose |
|--------|---------|
| `builtins/` | Pre-installed tools, commands, themes, skills |
| `themes/` | Theme loading and registry |
| `skills/` | Skill loading and injection |
| `prompts/` | Prompt template loading and substitution |

### Specialized

| Module | Purpose |
|--------|---------|
| `packages/` | Package/dependency management (reserved) |
| `rpc/` | JSON-RPC protocol for IDE integration |

## Agent Execution State Machine

The agent has three main states:

```
IDLE ──user input──> TURN ──no more tool calls──> IDLE
       ↑                ↓
       └─ tool results ─┘
```

| State | Meaning |
|-------|---------|
| `IDLE` | No active inference; waiting for user input |
| `TURN` | Processing a turn: calling inference, executing tools |

The state is exposed via `AgentPhase` enum in `agent/types.py` and accessible to extensions via hooks.

## Execution Phases in a Turn

Each agent turn follows this sequence:

```
1. Capture user input (TUI)
2. Add message to session
3. Call inference (LLM API)
4. Collect tool calls from response
5. Execute tools (in parallel if possible)
6. Add tool results to context
7. Call inference again (only if tools were called)
8. Render all messages and results (TUI)
9. Save session to disk
10. Fire completion hooks
```

## Message Types and Context

Messages in sessions are typed:

- `user` - User prompt
- `assistant` - LLM response (may contain tool calls)
- `tool` - Tool execution result
- `compaction` - Session compaction marker
- `branch_summary` - Summary when branching away

Each message includes metadata (timestamp, turn index, etc.) and is persisted to the session JSONL file.

## Context Building

Before each inference call, the runtime builds the context:

```
1. Load session from disk (JSONL)
2. Apply branching (load only messages on current branch)
3. Apply compaction (if context was summarized)
4. Inject skills (append skill markdown)
5. Inject prompts (template substitution)
6. Build system prompt (model-specific)
7. Add message history
8. Count tokens
```

Automatic compaction summarizes older messages when context approaches the limit. It is checked before sending a turn (pre-flight) and after each turn, and — as a reactive backstop — a request that still fails with a provider context-overflow error triggers a compact-and-retry (bounded to one attempt). See [Context Compaction](sessions.md#context-compaction).

## Hook & Event System

Extensions can react to lifecycle events:

- `session_start` - Session begins
- `turn_start` - New inference turn starts
- `turn_end` - Turn completes (before rendering)
- `tool_execute` - Tool is about to execute
- `tool_complete` - Tool execution finished
- `tui_ready` - TUI is ready for input
- `tui_exit` - TUI is exiting
- `compaction_start` - Compaction begins
- `compaction_end` - Compaction finishes

Hooks allow extensions to inspect state, modify messages, or react to events.

## Provider Abstraction

All LLM providers (Anthropic, OpenAI, Google, Mistral, Ollama, Azure) implement a common interface:

```python
class Provider:
    async def call(messages, tools, model) -> Response
    async def stream(messages, tools, model) -> AsyncIterator[Event]
```

The inference module handles provider-specific details (API keys, endpoints, format conversions).

## Tool Execution Model

Tools are executed in a sandboxed environment:

```
1. Tool call arrives from LLM
2. Trust/permission check (if enabled)
3. Tool execution starts (sync or async)
4. Tool streams partial results (optional)
5. Tool completes with final result
6. Result is serialized and added to context
```

Built-in tools (bash, read, write, edit, glob, grep, ls) are always available. Custom tools are registered via extensions.

## Extension Points

Tau is designed to be extended without modifying core code. Key extension points via the `tau` parameter in `register()`:

### Tools

Register custom tools that the agent can call:

```python
from tau.tool.types import Tool, ToolInvocation, ToolResult
from pydantic import BaseModel, Field

class MySchema(BaseModel):
    path: str = Field(..., description="File path")

class MyTool(Tool):
    name = "my_tool"
    description = "My custom tool"
    schema = MySchema
    
    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        # Implement tool logic
        return ToolResult.ok(invocation.id, "result")

def register(tau):
    tau.register_tool(MyTool())
```

### Commands

Add slash commands (`/command`):

```python
def register(tau):
    async def my_command(ctx, args):
        ctx.notify("Hello!")
    tau.register_command("my", "My command", my_command)
```

### Hooks

React to lifecycle events:

```python
def register(tau):
    async def on_tool_complete(event):
        print(f"Tool {event.tool_name} finished")
    tau.on_hook("tool_complete", on_tool_complete)
```

### Dialogs

Show modal dialogs to users:

```python
def register(tau):
    async def my_command(ctx, args):
        choice = await ctx.select("Pick one", ["A", "B", "C"])
```

### Themes

Add custom terminal color themes:

```python
# Save to ~/.tau/themes/my-theme.yaml
colors:
  primary: "#FF6B6B"
  secondary: "#4ECDC4"
  background: "#1A1A2E"
  text: "#EAEAEA"
```

See [Extensions](extensions.md) for detailed guides and more examples.

## Design Principles

1. **Separation of Concerns** - Each module has a single responsibility
2. **Provider Abstraction** - LLM providers hidden behind a unified `Provider` interface
3. **Extensibility First** - Extensions are first-class; everything can be customized
4. **Lazy Loading** - Modules loaded only when needed
5. **Type Safety** - Full type hints for IDE autocomplete and error checking
6. **Persistence** - Sessions and settings saved to disk for recovery and resumption
7. **Composability** - Modules compose cleanly; can be used independently

## Performance Characteristics

- **Startup**: ~0.5s (lazy loaded modules)
- **Message processing**: Depends on LLM (typically 1-30s)
- **Tool execution**: Sandbox overhead ~10ms per tool
- **Context window**: Automatic compaction when approaching limit
- **Memory**: ~50 MB baseline, grows with session size

## Security

- **Tool Execution**: Sandboxed, with optional permission prompts (trust system)
- **Credentials**: Encrypted credential storage (OS keychain or file)
- **Session Files**: Written to `~/.tau/sessions/` with 0600 permissions
- **Extensions**: Loaded from user-writable directories only

## Testing Strategy

- Unit tests for core logic (agent, inference, session)
- Integration tests for end-to-end flows
- Test fixtures for mocking providers and tools
- All modules have type hints for static analysis

## Next Steps

- [Project Structure](project-structure.md) - Detailed module breakdown
- [Usage Guide](usage.md) - Interactive mode and commands
- [Extensions Guide](extensions.md) - Building custom extensions
- [Python API](python-api.md) - Programmatic usage
