# Project Structure

Tau is one Python distribution, `tau/`, split into 25 top-level packages. This
page is the module inventory: what each package owns and which file to open
first. For how the packages layer and call each other, see
[Architecture](architecture.md).

## Table of Contents

- [Directory Tree](#directory-tree)
- [Execution Path](#execution-path)
- [Core Loop](#core-loop)
- [Data and State](#data-and-state)
- [Resources and Extensibility](#resources-and-extensibility)
- [Built-in Content](#built-in-content)
- [Terminal UI](#terminal-ui)
- [Support](#support)
- [Key Types](#key-types)
- [Where to Add Things](#where-to-add-things)

## Directory Tree

```text
tau/
├── __init__.py
├── agent/                  # Session-aware turn orchestration
│   ├── service.py          # Agent: invoke(), compact(), phase tracking
│   ├── types.py            # AgentPhase, AgentConfig, PromptOptions, ContextUsage
│   ├── embedded.py         # Run one isolated agent turn in-process
│   └── prompt/             # System prompt construction
│       ├── builder.py      # PromptBuilder, build_prompt(), project context files
│       └── types.py        # PromptOptions for the builder
├── auth/                   # Credential storage and resolution
│   ├── manager.py          # AuthManager: env vars, stored creds, OAuth refresh
│   ├── storage.py          # Credential file I/O
│   └── types.py
├── builtins/               # Everything shipped in the box
│   ├── tools/              # read, write, edit, glob, grep, ls, terminal
│   ├── commands/           # /new, /fork, /reload, /compact, /clear
│   ├── extensions/         # Bundled extensions (see below)
│   ├── models/             # Static model catalog: text, image, audio, video
│   ├── providers/          # Provider descriptors per modality
│   ├── prompts/            # commit, docs, explain, fix, refactor, review, test
│   ├── skills/             # code-review, debug, git-commit, skill-creator
│   └── themes/             # 17 bundled YAML themes
├── commands/               # Slash-command infrastructure
│   ├── registry.py         # CommandRegistry
│   └── types.py            # Command metadata, incl. idle-only declaration
├── console/                # CLI entry point
│   ├── cli.py              # Click app, mode resolution, print/json runners
│   └── commands/           # auth, doctor, packages, update subcommands
├── core/
│   └── registry.py         # Registry[T, E]: lazy 3-tier project→global→builtin base
├── engine/                 # Standalone inference + tool-execution loop
│   ├── service.py          # Engine
│   └── types.py            # EngineContext, EngineOptions, EngineState, queues
├── extensions/             # Plugin system
│   ├── api.py              # The `tau` object passed to register()
│   ├── loader.py           # Discovery, priority, and loading
│   ├── context.py          # ExtensionContext
│   ├── runtime.py          # Extension runtime and reload coordination
│   └── settings.py         # Extension settings schema validation
├── hooks/                  # Event bus, split by domain
│   ├── service.py          # Hooks: register/emit/subscribe
│   ├── types.py            # HookEvent union over every domain module
│   ├── engine.py           # Agent/turn/message/tool/compaction events
│   ├── inference.py        # before_provider_request, after_provider_response
│   ├── runtime.py          # Runtime, input, trust, terminal events
│   ├── session.py          # Session and branch-summary events
│   └── tui.py              # TUI lifecycle, queue, model/thinking selection
├── inference/              # LLM provider abstraction
│   ├── types.py            # StopReason, ThinkingLevel, contexts, options
│   ├── utils.py            # ErrorKind classification and retry
│   ├── api/                # Per-modality adapters
│   │   ├── text/           # 15 text APIs + TextLLM service
│   │   ├── image/          # OpenAI, Gemini, OpenRouter
│   │   ├── audio/          # OpenAI, Gemini, ElevenLabs, Sarvam
│   │   ├── video/          # fal, OpenRouter, ZAI
│   │   ├── registry.py
│   │   └── availability.py # Models whose provider has usable credentials
│   ├── model/
│   │   ├── registry.py     # ModelRegistry
│   │   ├── catalog.py      # Dynamic catalog backed by models.dev
│   │   ├── types.py        # Model, Cost, Modality
│   │   └── local/          # llamacpp, lmstudio, ollama, vllm discovery
│   └── provider/
│       ├── registry.py
│       ├── types.py
│       └── oauth/          # Claude Code, Codex, Copilot, Antigravity, xAI, PKCE
├── message/                # Message and content-block types
│   ├── types.py            # Roles, content blocks, message classes, unions
│   └── utils.py            # Media base64/MIME helpers, history filters
├── modes/                  # Run modes
│   ├── interactive/        # Full TUI application
│   │   ├── app.py          # Lifecycle and global shortcuts
│   │   ├── agent_hooks.py  # Agent events → UI state
│   │   ├── input_handler.py# Submit, queue, media, history
│   │   ├── ui_context.py   # Extension-facing UI customization
│   │   ├── commands/       # auth, context, extensions, misc, model, session, settings
│   │   └── components/     # Layout, message list, selectors, overlays, trust screen
│   ├── print/              # Namespace only; print and json modes run from console/cli.py
│   └── rpc/                # JSON-RPC over stdio for IDE integration
├── packages/               # Installed package management
│   ├── manager.py          # Resolution without path-traversal escape
│   ├── types.py
│   └── utils.py
├── prompts/                # Prompt template system
│   ├── loader.py           # Load templates from disk
│   ├── registry.py         # 3-tier registry
│   ├── expand.py           # Argument substitution
│   └── types.py
├── resources/              # Unified resource discovery
│   ├── loader.py           # ResourceLoader protocol + DefaultResourceLoader
│   └── types.py            # ResourceContext, ResourceSnapshot, diagnostics
├── runtime/                # Application wiring
│   ├── service.py          # Runtime: sessions, extensions, model switching
│   ├── dependencies.py     # RuntimeDependencies typed factories
│   └── types.py            # RuntimeConfig, RuntimeStartupResult
├── session/                # Persistence, branching, compaction
│   ├── manager.py          # SessionManager
│   ├── types.py            # Entry types, SessionInfo, SessionOptions
│   ├── compaction.py       # CompactionSettings and summarization
│   ├── branch_summarization.py
│   └── utils.py            # Session IDs, file scanning, to_llm_messages()
├── settings/               # Configuration
│   ├── manager.py          # SettingsManager: merge global + project
│   ├── storage.py          # File I/O
│   ├── types.py            # Every settings model
│   ├── paths.py            # CONFIG_DIR_PATH, sessions dir, app version
│   └── utils.py            # set_nested(), coerce_enum()
├── skills/                 # Skill loading and registry
│   ├── loader.py
│   ├── registry.py
│   └── types.py
├── telemetry/              # Opt-in PostHog install telemetry
│   ├── service.py          # Daemon-thread dispatch, exception autocapture
│   └── types.py            # BaseTelemetryEvent, InstallTelemetryEvent
├── themes/                 # Theme loading and registry
│   ├── loader.py
│   ├── registry.py
│   └── types.py
├── tool/                   # Tool abstractions
│   ├── types.py            # Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
│   ├── registry.py         # ToolRegistry — single source of truth
│   └── render.py           # Tool result display helpers
├── trust/                  # Per-directory trust decisions
│   ├── manager.py          # Persists to ~/.tau/trust.json
│   ├── types.py
│   └── utils.py
├── tui/                    # Standalone terminal UI framework
│   ├── service.py          # TUI, Renderer, overlays, focus
│   ├── terminal.py         # Terminal control and capability detection
│   ├── backend.py          # What a Terminal draws through
│   ├── frame.py            # Frame / BufferedTerminal double-buffered loop
│   ├── buffer.py           # Buffer / Cell grid
│   ├── widget.py           # Widget / StatefulWidget render contract
│   ├── layout.py           # Rect splitting
│   ├── geometry.py         # Rect
│   ├── style.py            # Style / Modifier / Color
│   ├── text.py             # Span / Line / Text
│   ├── palette.py          # Named color palettes
│   ├── ansi_bridge.py      # ANSI strings ↔ Buffer cells
│   ├── component.py        # Component and Container primitives
│   ├── components/         # editor, text_input, select_list, spinner, image, box
│   ├── widgets/            # block, paragraph, list, table, tabs, chart, barchart,
│   │                       #   sparkline, gauge, calendar, canvas, scrollbar, clear
│   ├── input.py            # Input events, terminal parser, keybinding registry
│   ├── autocomplete.py     # Generic autocomplete management
│   ├── markdown.py         # Markdown → ANSI
│   ├── keybinding_hints.py # Keybinding hint formatting
│   ├── theme.py            # TUI theme types
│   ├── testing.py          # Buffer/Widget render test helpers
│   └── utils.py
└── utils/                  # Cross-cutting helpers
    ├── format.py           # human_size and friends
    ├── fs.py               # Filesystem helpers
    ├── http_proxy.py       # Proxy resolution from settings or env
    ├── image_processing.py
    ├── logging.py          # Session log files
    ├── profiling.py        # TAU_PROFILE=1 span timing
    ├── secrets.py          # Resolve secret references to values
    ├── ssl_context.py
    ├── timing.py           # Startup timing report
    └── version_check.py    # PyPI update check
```

## Execution Path

`console/cli.py` resolves one of four run modes and hands off to a `Runtime`.

| Mode | Selected by | Runs |
|------|-------------|------|
| `interactive` | Default on a TTY | `modes/interactive/app.py` → `App` |
| `print` | `--print`/`-p`, a positional prompt, or non-TTY stdout | `_run_print()` in `console/cli.py` |
| `json` | A prompt plus `--output-format json` | `_run_json()` in `console/cli.py` |
| `rpc` | `--mode rpc` | `modes/rpc/mode.py` → `run_rpc_mode()` |

`tau/modes/print/` is an empty namespace package; print and json mode logic lives
in `console/cli.py`.

`console/commands/` holds the non-agent CLI subcommands:

| Module | Subcommand |
|--------|-----------|
| `auth.py` | `tau auth` — provider login and credential management |
| `doctor.py` | `tau doctor` — settings, model, and extension diagnostics with `--fix` |
| `packages.py` | `tau install` / `tau list` / `tau remove` |
| `update.py` | `tau update` |

## Core Loop

Three packages form the request path. Each is a strict layer over the next.

| Package | Owns | Does not own |
|---------|------|--------------|
| `runtime/` | Wiring, sessions, extensions, model switching, tree navigation | Turn mechanics |
| `agent/` | One session-aware turn: context building, compaction, persistence, retry | Streaming, tool dispatch |
| `engine/` | Streaming, tool validation and dispatch, steering/follow-up queues | Sessions, extensions, UI |
| `inference/` | Provider auth, endpoints, request shaping, normalized event stream | Tool execution |

`agent/prompt/builder.py` builds the system prompt: OS, machine, and shell
detection, git status with redacted remote URLs, and project context files
(`AGENTS.md` / `CLAUDE.md`) discovered from the git root down to the current
directory.

`agent/embedded.py` runs a single isolated agent turn in-process — useful for
subagent-style delegation without spawning a runtime.

## Data and State

| Package | Purpose |
|---------|---------|
| `session/` | JSONL persistence, branching tree, compaction, branch summaries |
| `message/` | Message and content-block types — see [Messages](messages.md) |
| `settings/` | Global + project JSON settings, merged with explicit field tracking |
| `auth/` | API keys from env or encrypted store, plus OAuth token refresh |
| `trust/` | Per-directory trust decisions persisted to `~/.tau/trust.json` |
| `telemetry/` | Opt-in PostHog install event, dispatched on a daemon thread |

## Resources and Extensibility

| Package | Purpose |
|---------|---------|
| `resources/` | One `ResourceSnapshot` per startup or `/reload`, plus diagnostics |
| `extensions/` | Extension discovery, loading, API surface, and reload coordination |
| `hooks/` | Typed event bus consumed by extensions and the TUI |
| `commands/` | Slash-command registry and metadata |
| `tool/` | `Tool` base class and the registry every tool resolves through |
| `packages/` | Installed-package resolution, guarded against traversal and symlink escape |
| `skills/`, `prompts/`, `themes/` | 3-tier registries built on `core/registry.py` |

`core/registry.py` defines `Registry[T, E]`, the lazy three-tier base class used
by the skill, prompt, and theme registries. Load priority is project → global →
builtin, highest wins.

## Built-in Content

| Directory | Contents |
|-----------|----------|
| `builtins/tools/` | `read`, `write`, `edit`, `glob`, `grep`, `ls`, `terminal` |
| `builtins/commands/` | `/new` and `/fork` (from `session.py`), `/reload`, `/compact`, `/clear` |
| `builtins/extensions/` | `footer/` (git + model status), `header/`, `todo/` (todo tool), `web/` (search and fetch) |
| `builtins/models/` | Static model definitions for text, image, audio, video |
| `builtins/providers/` | Provider descriptors per modality |
| `builtins/prompts/` | `commit`, `docs`, `explain`, `fix`, `refactor`, `review`, `test` |
| `builtins/skills/` | `code-review`, `debug`, `git-commit`, `skill-creator` |
| `builtins/themes/` | `dark`, `light`, `ayu-dark`, `catppuccin`, `dracula`, `everforest`, `gruvbox`, `horizon`, `kanagawa`, `material-ocean`, `monokai`, `night-owl`, `nord`, `one-dark`, `rose-pine`, `solarized-dark`, `tokyo-night` |

The `web` extension supports pluggable search engines under
`builtins/extensions/web/engines/`: DuckDuckGo (`ddgs`), Exa, Jina, and Tavily.
`todo` and `web` each ship a `manifest.json`.

Interactive-only commands live in `modes/interactive/commands/`, not
`builtins/commands/`.

## Terminal UI

`tau/tui/` is a standalone framework with no dependency on the rest of Tau. It
has two layers:

| Layer | Files | Role |
|-------|-------|------|
| Render core | `buffer.py`, `widget.py`, `frame.py`, `backend.py`, `geometry.py`, `layout.py`, `style.py`, `text.py`, `palette.py` | Immediate-mode widgets drawn into a double-buffered cell grid |
| Component core | `service.py`, `component.py`, `components/`, `input.py`, `autocomplete.py` | Retained components, focus, overlays, differential rendering |

`ansi_bridge.py` converts between the two representations. `widgets/` holds the
render-core widget library: `block`, `paragraph`, `list`, `table`, `tabs`,
`chart`, `barchart`, `sparkline`, `gauge`, `calendar`, `canvas`, `scrollbar`,
`clear`, and shared `symbols`. `testing.py` provides render assertions.

Tau-specific UI composition lives in `modes/interactive/`, never in `tui/`. See
[Terminal UI](tui.md).

## Support

| Module | Purpose |
|--------|---------|
| `utils/secrets.py` | Resolve secret references (API keys, tokens, proxy creds) to values |
| `utils/http_proxy.py` | Proxy resolution from settings or environment — see [HTTP Proxy](http-proxy.md) |
| `utils/profiling.py` | Opt-in span timing via `TAU_PROFILE=1` |
| `utils/timing.py` | Startup timing report |
| `utils/version_check.py` | PyPI update check for the installed distribution |
| `utils/logging.py` | Per-session log files under `~/.tau/logs/` |

## Key Types

### Engine

`engine/service.py` — the standalone loop. Full API in [Engine](engine.md).

```python
class Engine:
    def __init__(self, cwd: Path, llm: TextLLM, tools: list[Tool],
                 system_prompt: str | None = None,
                 options: EngineOptions | None = None,
                 hooks: Hooks | None = None,
                 settings: SettingsManager | None = None) -> None: ...

    async def run(self, ctx: EngineContext, signal: AbortSignal | None = None) -> None
    async def run_continue(self, signal: AbortSignal | None = None) -> None
    async def subscribe(self, handler) -> Callable[[], None]
    def abort(self) -> None
```

### Agent

`agent/service.py` — the session-aware layer above `Engine`.

```python
class Agent:
    @property
    def phase(self) -> AgentPhase          # idle | turn | compaction | branch_summary
    @property
    def is_idle(self) -> bool
    async def invoke(self, text: str, options: PromptOptions | None = None) -> None
    async def compact(self, custom_instructions: str | None = None) -> bool
    def get_context_usage(self) -> ContextUsage | None
    def abort(self) -> None
```

### Runtime

`runtime/service.py` — wires agent, session, engine, and extensions.

```python
class Runtime:
    @classmethod
    async def create(cls, config: RuntimeConfig) -> Runtime
    @classmethod
    async def create_with_result(cls, config: RuntimeConfig) -> RuntimeStartupResult

    async def user_input(self, text: str, options: PromptOptions | None = None) -> None
    async def invoke(self, text: str, options: PromptOptions | None = None, *,
                     display: bool = False) -> None
    async def steer(self, message: str) -> None
    async def follow_up(self, message: str) -> None
    async def execute_terminal(self, cmd: str, exclude: bool = False) -> None
    async def set_model(self, model_id: str, provider: str | None = None) -> bool
    async def new_session(self, *, with_session=None) -> None
    async def resume_session(self, session_file: Path, *, with_session=None) -> None
    async def fork_session(self, ...) -> None
    async def clone_session(self) -> None
    async def navigate_tree(self, ...) -> None
    async def reload_extensions(self)
    async def ashutdown(self) -> None
```

### Tool

`tool/types.py` — note that `name`, `kind`, and `execution_mode` are
**constructor arguments**, not class attributes.

```python
class Tool(ABC):
    def __init__(self, name: str, description: str, schema: type[BaseModel],
                 kind: ToolKind,
                 execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
                 *, render_call=None, render_result=None, render_shell="self",
                 result_expandable=True, result_preview_lines=None,
                 prompt_snippet=None, prompt_guidelines=None,
                 prepare_arguments=None) -> None: ...
```

## Where to Add Things

| To add | Put it in | Registered by |
|--------|-----------|---------------|
| A built-in tool | `builtins/tools/` | `ToolRegistry` |
| A built-in slash command | `builtins/commands/` | `CommandRegistry` |
| An interactive-only command | `modes/interactive/commands/` | `CommandRegistry` |
| A text provider API | `inference/api/text/` | `inference/api/text/registry.py` |
| A model definition | `builtins/models/text.py` | `ModelRegistry` |
| An OAuth flow | `inference/provider/oauth/` | `ProviderRegistry` |
| A theme | `builtins/themes/*.yaml` or `~/.tau/themes/` | `themes/registry.py` |
| A skill | `builtins/skills/<name>/SKILL.md` or `~/.tau/skills/` | `skills/registry.py` |
| A prompt template | `builtins/prompts/*.md` or `~/.tau/prompts/` | `prompts/registry.py` |
| A render-core widget | `tui/widgets/` | Imported directly |
| A retained UI component | `tui/components/` | Imported directly |
| A hook event | The matching `hooks/*.py`, then the union in `hooks/types.py` | `Hooks` |

User-authored extensions do not go in this tree at all — see
[Extensions](extensions.md).

## Next Steps

- [Architecture](architecture.md) - layering, data flow, and boundaries
- [Engine](engine.md) - the embeddable loop
- [Development](development.md) - local setup and testing
- [Extensions](extensions.md) - extending Tau without touching this tree
