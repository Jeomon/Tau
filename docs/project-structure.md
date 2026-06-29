# Project Structure

Tau consists of 205 Python modules organized into 22 main packages. This page documents the codebase organization and provides module-by-module reference for contributors and agents.

## Directory Structure

```
tau/                                # Main package (205 modules)
├── __init__.py
├── content_registry.py             # Content registry abstractions
├── agent/                          # Agent execution service
├── auth/                           # Authentication & credential management
├── builtins/                       # Built-in tools, commands, themes, skills
│   ├── tools/                      # Pre-installed tools (bash, read, write, etc.)
│   ├── commands/                   # Built-in slash commands
│   ├── themes/                     # Default themes (dracula, nord, etc.)
│   ├── providers/                  # Built-in LLM provider configurations
│   ├── prompts/                    # Built-in prompt templates
│   └── skills/                     # Built-in skills
├── commands/                       # Slash command system
├── console/                        # CLI entry point
├── engine/                         # Tool execution engine
├── extensions/                     # Plugin system & API
├── hooks/                          # Event hook system (modular hooks)
├── inference/                      # LLM provider abstraction
│   ├── api/                        # Provider API definitions
│   ├── provider/                   # Provider implementations
│   ├── model/                      # Model registry
│   ├── types.py
│   └── utils.py
├── message/                        # Message types and utilities
├── packages/                       # Package/dependency management
├── prompts/                        # Prompt template system
├── rpc/                            # JSON-RPC protocol for IDE integration
├── runtime/                        # Agent runtime service
├── session/                        # Session management and persistence
├── settings/                       # Configuration system
├── skills/                         # Skill loading and registry
├── themes/                         # Theme loading and registry
├── tool/                           # Tool abstractions and registry
├── trust/                          # Trust and permission system
└── tui/                            # Terminal user interface (22 files)

docs/                              # Documentation
tests/                             # Test suite
README.md                          # Project overview
pyproject.toml                     # Project metadata and dependencies
```

## Module Breakdown

### `agent/` - Agent Execution Service

Core agent that processes messages and manages inference.

- `service.py` - Main agent execution logic
- `types.py` - Agent state types (`AgentPhase`)

### `auth/` - Authentication & Credentials

Credential storage and resolution for LLM providers.

- `manager.py` - Load, cache, and resolve credentials
- `storage.py` - Encrypted credential file operations
- `types.py` - Auth data structures

### `builtins/` - Built-in Tools, Commands, Themes

Pre-installed functionality available to all users.

**Tools** (`tools/`):
- `terminal.py` - Execute shell commands
- `read.py` - Read file contents
- `write.py` - Create/overwrite files
- `edit.py` - Edit existing files
- `glob.py` - File globbing
- `grep.py` - Search files by regex
- `ls.py` - List directory contents

**Commands** (`commands/`) - Slash commands:
- `model.py` - `/model` command (model selection)
- `theme.py` - `/theme` command (theme picker)
- `session.py` - Session management (`/new`, `/resume`, `/fork`, etc.)
- `help.py` - `/help` command

**Themes** (`themes/`) - Default color schemes:
- `default.yaml`, `dracula.yaml`, `nord.yaml`, `gruvbox.yaml`, `catppuccin.yaml`

**Prompts** (`prompts/`) - System prompts for agent context

**Skills** (`skills/`) - Default agent instruction sets

### `commands/` - Slash Command System

Infrastructure for command registration and execution.

- `registry.py` - Command registration and lookup
- `types.py` - Command data structures

### `console/` - CLI Entry Point

CLI argument parsing and initialization.

- `cli.py` - Main CLI function

### `engine/` - Tool Execution

Executes tools with sandboxing and result collection.

- `service.py` - Main tool execution engine
- `types.py` - Tool execution data structures

### `extensions/` - Plugin System

Loading and API for custom extensions.

- `api.py` - Extension API (35 KB, main extension interface)
- `loader.py` - Extension discovery and loading
- `context.py` - Extension runtime context
- `runtime.py` - Runtime context management
- `events.py` - Extension events

### `hooks/` - Event Hook System

Modular event system for reacting to lifecycle events.

- `service.py` - Hook registration and execution
- `types.py` - Hook definitions
- `engine.py` - Engine hooks
- `inference.py` - Inference hooks
- `runtime.py` - Runtime hooks
- `session.py` - Session hooks
- `tui.py` - TUI hooks

### `inference/` - LLM Provider Abstraction

Unified interface to multiple LLM providers.

- `types.py` - Inference types (models, responses, streaming)
- `utils.py` - Provider utilities
- **api/** - Provider API abstractions
- **provider/** - Provider implementations
  - `registry.py` - Provider registry
  - `types.py` - Provider types
  - `oauth/` - OAuth flow implementations
- **model/** - Model registry and enumeration

### `message/` - Message Types

Message data structures and utilities.

- `types.py` - Message types and enums
- `utils.py` - Message utilities

### `packages/` - Package Management

Package/dependency resolution (reserved for future use).

- `manager.py` - Package management
- `types.py` - Package types
- `utils.py` - Package utilities

### `prompts/` - Prompt Template System

Prompt loading and variable substitution.

- `loader.py` - Load prompts from files
- `registry.py` - Prompt registry
- `expand.py` - Argument substitution
- `types.py` - Prompt types

### `rpc/` - JSON-RPC Protocol

JSON-RPC server for IDE integration.

- `mode.py` - RPC mode implementation
- `types.py` - RPC message types

### `runtime/` - Agent Runtime

Wires together agent, session, engine, and extensions.

- `service.py` - Main runtime orchestration
- `types.py` - Runtime state types

### `session/` - Session Management

Session persistence, branching, and compaction.

- `manager.py` - Session CRUD operations
- `types.py` - Session data structures
- `compaction.py` - Context compaction logic
- `branch_summarization.py` - Branch summarization
- `utils.py` - Session utilities

### `settings/` - Configuration System

Loads and manages settings from JSON files.

- `manager.py` - Load/merge settings
- `storage.py` - File I/O operations
- `types.py` - All setting types
- `paths.py` - Settings file paths

### `skills/` - Skill System

Loads and injects skills (agent instruction sets) into context.

- `loader.py` - Load skill files
- `registry.py` - Skill registry
- `types.py` - Skill data structures

### `themes/` - Theme System

Loads and manages terminal color themes.

- `loader.py` - Load YAML/JSON themes
- `registry.py` - Theme registry
- `types.py` - Theme data structures

### `tool/` - Tool Abstractions

Tool registration and execution interface.

- `registry.py` - Tool registry
- `types.py` - Tool base classes and types
- `render.py` - Tool result rendering

### `trust/` - Trust & Permissions

Trust and permission system for tool execution.

- `manager.py` - Trust and permission checks
- `types.py` - Trust data structures
- `utils.py` - Trust utilities

### `tui/` - Terminal UI Primitives

Reusable terminal parsing, differential rendering, components, themes, and
keybindings.

- `tui.py` - Main event loop, focus, overlays, and differential rendering
- `terminal.py` - Terminal control and capability detection
- `input.py` - Generic input events, terminal parser, and keybinding registry
- `component.py` - Component and container primitives
- `components/` - Editor, text input, selectors, spinner, images, and boxes
- `autocomplete.py` - Generic autocomplete management
- `markdown.py` - Markdown-to-ANSI rendering
- `theme.py` - TUI theme types
- `ui_context.py` - Extension-facing UI context

### `modes/interactive/` - Interactive Application

Interactive runtime orchestration and Tau-specific UI composition.

- `app.py` - Application lifecycle and global shortcuts
- `agent_hooks.py` - Agent-event to UI-state projection
- `input_handler.py` - Runtime-aware submit, queue, media, and history orchestration
- `commands/` - Interactive slash-command implementations
- `components/layout.py` - Editor-zone composition
- `components/message_list.py` - Message and tool-result rendering
- `components/selector_controller.py` - Inline selector lifecycle and input routing
- `components/overlays.py` - Interactive dialogs and editors
- `ui_context.py` - UI state context

## Key Types and Classes

### Agent Service

`agent/service.py` - Main agent that processes messages.

```python
class AgentService:
    async def process_turn(ctx: AgentContext) -> None
    # Executes inference and collects tool results
```

### Tool

`tool/types.py` - Tool base class for custom tools.

```python
class Tool(ABC):
    name: str
    description: str
    schema: BaseModel
    
    async def execute(invocation: ToolInvocation) -> ToolResult
```

### Runtime

`runtime/service.py` - Orchestrates agent, session, engine, extensions.

```python
class RuntimeService:
    async def start_agent() -> None
    async def execute_tool(...) -> ToolResult
    async def compact_context() -> None
```

### Extension API

`extensions/api.py` - Main extension interface for plugins (31 KB).

Provides methods to register tools, commands, hooks, dialogs, etc.

### Hook System

`hooks/service.py` - Event hooks for lifecycle events.

Hooks available:
- `session_start`, `session_end`
- `turn_start`, `turn_end`
- `tool_execute`, `tool_complete`
- `tui_ready`, `tui_exit`
- `compaction_start`, `compaction_end`

## Data Flow

User input flows through these modules in sequence:

```
1. Console (cli.py)
   └─ Parses CLI args, selects run mode

2. Session Manager (session/manager.py)
   └─ Loads or creates session, builds message history

3. TUI (tui/tui.py)
   └─ Renders interface, captures user input

4. Runtime (runtime/service.py)
   └─ Wires together agent, extensions, engine

5. Agent Service (agent/service.py)
   └─ Processes turn: calls inference, collects tool calls

6. Inference (inference/provider/)
   └─ Calls LLM provider API, streams response

7. Engine (engine/service.py)
   └─ Executes tool calls, collects results

8. TUI Renderer (tui/renderer.py)
   └─ Renders messages and tool results

9. Session Manager (session/manager.py)
   └─ Persists session to disk (JSONL format)

10. Hooks (hooks/service.py)
    └─ Fires events for extensions to react to
```

## Module Dependencies

```
console
  └─ settings, auth
     └─ session → agent ─┐
                    ├─ inference → provider
                    └─ engine ─ builtins, tools
                       └─ trust
                       └─ hooks
                          └─ extensions
                             └─ tui
                                └─ themes
```

## Configuration Hierarchy

Settings are merged in priority order:

```
1. Built-in defaults (code)
2. ~/.tau/settings.json (global user settings)
3. .tau/settings.json (project settings)
4. Environment variables (ANTHROPIC_API_KEY, TAU_PROVIDER, etc.)
5. Command-line flags (--model, --provider, --theme)
```

Higher priority overrides lower priority.

## Extension Points

Key locations to extend Tau:

| Extension Type | Module | How to Add |
|----------------|--------|-----------|
| **Custom Tools** | `extensions/api.py` | `tau.register_tool(MyTool())` |
| **Slash Commands** | `extensions/api.py` | `tau.register_command("cmd", ...)` |
| **Hooks/Events** | `hooks/service.py` | `tau.on_hook("event_name", callback)` |
| **Themes** | `themes/loader.py` | YAML files in `~/.tau/themes/` |
| **Skills** | `skills/loader.py` | Markdown files in `~/.tau/skills/` |
| **Prompts** | `prompts/loader.py` | Template files in `~/.tau/prompts/` |
| **LLM Providers** | `inference/provider/` | Implement provider interface |

See [Extensions Guide](extensions.md) for detailed examples.

## Code Statistics

- **Total modules**: 205 Python files
- **Main package**: tau/ (22 subpackages)
- **Test coverage**: tests/ directory
- **Lines of code**: ~8,000 LOC (excluding tests and docs)
- **Type hints**: Full type coverage with mypy/pyright

## Next Steps

- [Architecture Guide](architecture.md) - System design and data flow diagrams
- [Development Setup](development.md) - Local development environment
- [Extensions Guide](extensions.md) - Complete guide to extending Tau
- [All Docs](index.md) - Full documentation index
