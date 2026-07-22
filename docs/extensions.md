# Extensions

Extensions are plain Python modules that extend Tau's behaviour. Each one exports a
single `register(tau)` function and, from there, registers tools the model can call,
slash commands, keyboard shortcuts, lifecycle event handlers, TUI widgets and dialogs,
themes, autocomplete providers, and even whole LLM providers.

Use an extension when you want to change what Tau *does*. Use a [skill](skills.md) when
you only want to change what the model *knows*. For a focused walkthrough of writing a
single tool, see [Creating Tools](creating-tools.md); for per-extension configuration
schemas and the `/settings` panel, see [Extension Settings](extension-settings.md).

> **Security:** Extensions execute arbitrary Python in Tau's own process, with your full
> user permissions. Only install extensions from sources you trust. Project-local
> extensions under `.tau/extensions/` load only after the project is trusted.

Working, maintained implementations live in `examples/extensions/` in this repository —
`todo`, `web`, `lsp`, `sandbox`, `subagent`, `workflow`, `loop`, `peer`, `ask_user`,
`autoresearch`, `computer_use`, and `voice`. They are referenced throughout this document and are the
best starting point for anything non-trivial.

## Table of Contents

- [Concepts](#concepts)
- [Quick Start](#quick-start)
- [Locations and Discovery](#locations-and-discovery)
  - [Discovery order](#discovery-order)
  - [Extension layouts](#extension-layouts)
  - [manifest.json reference](#manifestjson-reference)
  - [Declared dependencies](#declared-dependencies)
  - [Same-identity priority](#same-identity-priority)
  - [Shipping extensions as packages](#shipping-extensions-as-packages)
- [The register() Entry Point](#the-register-entry-point)
  - [Inline extension factories](#inline-extension-factories)
- [Custom Tools](#custom-tools)
- [Slash Commands](#slash-commands)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Event Hooks](#event-hooks)
  - [Dispatch model](#dispatch-model)
  - [Lifecycle overview](#lifecycle-overview)
  - [Runtime and reload events](#runtime-and-reload-events)
  - [TUI events](#tui-events)
  - [Session events](#session-events)
  - [Agent and turn events](#agent-and-turn-events)
  - [Message events](#message-events)
  - [Tool events](#tool-events)
  - [Input and terminal events](#input-and-terminal-events)
  - [Provider events](#provider-events)
  - [Compaction events](#compaction-events)
  - [Model, queue, and persistence events](#model-queue-and-persistence-events)
  - [Reserved events that never fire](#reserved-events-that-never-fire)
- [Interception Recipes](#interception-recipes)
  - [Custom compaction](#custom-compaction)
  - [Ephemeral context injection](#ephemeral-context-injection)
  - [Rewriting user input](#rewriting-user-input)
  - [Intercepting shell commands](#intercepting-shell-commands)
  - [Contributing resource paths](#contributing-resource-paths)
  - [Deciding project trust](#deciding-project-trust)
- [TUI: Widgets, Dialogs, and Overlays](#tui-widgets-dialogs-and-overlays)
- [Custom Message Renderers](#custom-message-renderers)
- [Editor Autocomplete Providers](#editor-autocomplete-providers)
- [Themes](#themes)
- [Custom LLM Providers](#custom-llm-providers)
- [Inter-Extension Services](#inter-extension-services)
- [Session Persistence](#session-persistence)
- [Configuration and Settings](#configuration-and-settings)
- [Flags](#flags)
- [Shell Execution](#shell-execution)
- [API Reference](#api-reference)
  - [ExtensionAPI](#extensionapi)
  - [ExtensionContext](#extensioncontext)
  - [UIContext](#uicontext)
- [End-to-End Example](#end-to-end-example)
- [Hot Reload](#hot-reload)
- [Debugging](#debugging)
- [Next Steps](#next-steps)

## Concepts

An extension is a Python file (or package directory) discovered from a known location.
Tau imports it, looks for a module-level `register` callable, and calls it with an
`ExtensionAPI` object — conventionally named `tau`. Everything registered there is
collected into an `Extension` record and applied to the live runtime once every
extension has loaded.

There is no base class to subclass, no ABC, and no plugin entry-point metadata. The
entire contract is:

```python
def register(tau):
    ...
```

Two API objects matter, and they are available at different times:

| | `ExtensionAPI` (`tau`) | `ExtensionContext` (`ctx`) |
|---|---|---|
| Available in | `register(tau)` | Event, command, and shortcut handlers |
| Represents | Static wiring, applied after load | A live snapshot of the running session |
| Typical use | `tau.register_tool(...)`, `tau.on(...)` | `ctx.ui`, `ctx.branch_entries`, `ctx.model_id` |
| Session state | Not yet available | Available |

Capabilities an extension can register:

| Capability | API | Section |
|---|---|---|
| Tools the model can call | `tau.register_tool(tool)` | [Custom Tools](#custom-tools) |
| Slash commands | `tau.register_command(...)` | [Slash Commands](#slash-commands) |
| Keyboard shortcuts | `tau.register_shortcut(...)` | [Keyboard Shortcuts](#keyboard-shortcuts) |
| Lifecycle event handlers | `tau.on(event, handler)` | [Event Hooks](#event-hooks) |
| System-prompt additions | `tau.append_prompt(text)` | [The register() Entry Point](#the-register-entry-point) |
| TUI widgets, dialogs, overlays | `ctx.ui.*` | [TUI](#tui-widgets-dialogs-and-overlays) |
| Custom message rendering | `tau.register_message_renderer(...)` | [Message Renderers](#custom-message-renderers) |
| Editor autocomplete | `tau.add_autocomplete_provider(...)` | [Autocomplete](#editor-autocomplete-providers) |
| Themes | `tau.register_theme(...)` | [Themes](#themes) |
| LLM providers and models | `tau.register_provider(...)` | [Custom Providers](#custom-llm-providers) |
| Shared service objects | `tau.provide(...)` / `tau.get_service(...)` | [Services](#inter-extension-services) |
| Durable session state | `tau.append_entry(...)` | [Session Persistence](#session-persistence) |
| `/settings` sub-panels | `tau.register_settings(...)` or `manifest.json` | [Extension Settings](extension-settings.md) |
| Skills shipped alongside code | `manifest.json` `"skills"` | [manifest.json](#manifestjson-reference) |

Hot reload is supported: `/reload` re-runs every `register(tau)` against the live
session without restarting Tau. See [Hot Reload](#hot-reload).

## Quick Start

Create `.tau/extensions/greeter.py` in your project:

```python
# .tau/extensions/greeter.py
from pydantic import BaseModel, Field
from tau.tool.types import Tool, ToolInvocation, ToolKind, ToolResult


class GreetSchema(BaseModel):
    name: str = Field(..., description="Who to greet")


class GreetTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="greet",
            description="Greet someone by name.",
            schema=GreetSchema,
            kind=ToolKind.Read,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ) -> ToolResult:
        name = invocation.params["name"]
        return ToolResult.ok(invocation.id, f"Hello, {name}!")


def register(tau):
    # A tool the model can call
    tau.register_tool(GreetTool())

    # A slash command
    async def cmd_hello(ctx, args):
        if ctx.ui:
            ctx.ui.notify(f"Hello, {args[0] if args else 'world'}!")

    tau.register_command("hello", "Say hello", cmd_hello, argument_hint="<name>")

    # A lifecycle hook
    @tau.on("session_start")
    async def on_start(event, ctx):
        if ctx.ui:
            ctx.ui.set_status("greeter", "greeter loaded")
```

Start Tau in that directory. `/hello world` runs the command, the model can call
`greet`, and the footer shows the status slot. Edit the file and run `/reload` to apply
changes without restarting.

## Locations and Discovery

### Discovery order

Extensions are discovered from four sources, scanned in this order:

| Order | Location | Source label | Scope |
|---|---|---|---|
| 1 | `tau/builtins/extensions/` (bundled) | `builtin` | Ships with Tau |
| 2 | `<cwd>/.tau/extensions/` | `project` | This project only |
| 3 | `~/.tau/extensions/` | `global` | All projects |
| 4 | Enabled entries in `extensions.list` (settings.json) | `explicit` | Named paths |

In addition, `RuntimeConfig.extension_factories` supplies in-memory extensions with the
source label `inline`, and installed packages contribute extensions labelled `package`.

Within a directory, entries are sorted alphabetically and scanned **one level deep**:

- Any entry whose name starts with `_` is skipped.
- A `*.py` file is loaded directly, unless its stem is disabled.
- A subdirectory is resolved via its `manifest.json` or `__init__.py` (see below), and
  is skipped if its directory name is disabled.
- Duplicate resolved paths are silently deduplicated.

Disabling is driven by `extensions.list[].enabled: false` in `settings.json`, or
interactively from the `/extensions` panel.

A failing extension is recorded as an `ExtensionError` and reported — it never crashes
startup, and the other extensions still load.

### Extension layouts

**Single file** — the simplest case:

```text
.tau/extensions/
└── greeter.py            # must define register(tau)
```

**Package directory** — a folder whose `__init__.py` is the entry point:

```text
.tau/extensions/
└── web/
    ├── __init__.py       # register(tau) lives here
    ├── engines/
    │   └── ddgs.py
    └── tools/
        └── search.py
```

A directory extension is loaded as a **package**: the loader calls
`importlib.util.spec_from_file_location(name, dir/"__init__.py")`, and Python makes that
a package because the entry file is `__init__.py`. Import siblings relatively:

```python
# web/__init__.py
from .tools.search import WebSearchTool


def register(tau):
    tau.register_tool(WebSearchTool())
```

Every example in `examples/extensions/` uses exactly this pattern.

> **Do not add the extension directory to `sys.path` and import siblings by bare name.**
> Each extension gets a unique package name (`_tau_ext_<hash of path>`), so relative
> imports are private to it — but a bare `import state` goes into the process-wide
> `sys.modules`, where the first extension to claim the name wins and every later one
> silently gets *that* module. Generic names (`state`, `tool`, `types`, `utils`, `model`)
> collide across extensions in practice: it once left the workflow extension calling the
> subagent extension's `discover_agents()`, which returns a different shape and crashed.

**Manifest-declared entry points** — for a named entry file, multiple entry files, or
any declared dependencies, skills, or settings schema:

```text
.tau/extensions/
└── web/
    ├── manifest.json     # declares entries, deps, settings schema
    ├── main.py
    └── tools/
        ├── __init__.py   # needed for `tools` to import as a package
        └── search.py
```

```json
{
  "tau": {
    "extensions": ["./main.py"]
  }
}
```

Each declared file must export its own `register(tau)`, and they load in declaration
order.

Choosing between them:

| Layout | Best for |
|---|---|
| Single `.py` file | One tool, one command, or a couple of hooks |
| Directory + `__init__.py` | A coherent extension with internal modules |
| Directory + `manifest.json` | Dependencies, a settings schema, bundled skills, or a non-default entry name |

### manifest.json reference

All extension metadata lives under the `"tau"` key:

```json
{
  "tau": {
    "name": "Todo",
    "author": "jeomon",
    "extensions": ["./main.py"],
    "dependencies": ["ddgs>=9.0"],
    "skills": ["skills"],
    "settings": {
      "title": "Todo",
      "fields": [
        {
          "key": "enabled",
          "label": "Enabled",
          "type": "bool",
          "default": true,
          "description": "Register the todo tool and /todos command."
        }
      ]
    }
  }
}
```

| Field | Type | Effect |
|---|---|---|
| `name` | string | Display name in the `/extensions` panel |
| `author` | string | Author shown in the `/extensions` panel |
| `extensions` | list[string] | Entry files, relative to the manifest. Omit to fall back to `__init__.py` |
| `dependencies` | list[string] | pip specs installed before the entry file runs |
| `skills` | list[string] | Directories of skills registered at discovery time |
| `settings` | object | Schema that auto-generates a `/settings` sub-panel — see [Extension Settings](extension-settings.md) |

A manifest declaring only `dependencies` (no `extensions`) is valid; the loader still
falls back to `__init__.py` as the entry point.

### Declared dependencies

Dependencies from `manifest.json` are installed before the entry file executes, using
`uv pip install` and falling back to the target venv's own `pip` when `uv` is not on
`PATH`. The resolved venv's `site-packages` is then appended to `sys.path` so the
extension's imports resolve in the running process.

Target venv selection:

| Extension source | Installs into |
|---|---|
| `project` | `<cwd>/.venv` when it exists **and** its Python major.minor matches the interpreter running Tau |
| `project` with a mismatched `.venv` | The running interpreter's own environment (`sys.prefix`) |
| Everything else | The global packages venv, `~/.tau/venv` |

The version check exists because native (C-extension) wheels are built per Python
version, and the resolved `site-packages` is added to *this* process's `sys.path`; a
mismatch would make those imports fail at runtime.

Installation runs once per dependency set. A SHA-256 digest of the sorted specs is
cached in `<venv>/.tau_ext_deps.json`, keyed by extension directory, and cross-process
access is serialized with a file lock. **Failures are cached too** — a spec that cannot
build on this interpreter fails fast on subsequent launches instead of retrying the
whole install every time. Tau's own runtime environment keeps precedence, so an
extension cannot downgrade Tau's dependencies out from under it.

### Same-identity priority

Path deduplication only catches the same file discovered twice. When the same extension
exists as physically separate copies under two sources — for example a builtin also
copied into `~/.tau/extensions/` — only the highest-priority copy loads:

```text
project  >  global  >  builtin
```

Identity is the folder name for a package entry point, and the file stem otherwise.
Unranked sources (`explicit`, `inline`, `package`) always pass through: they never
suppress a ranked entry, and are never suppressed by one.

### Shipping extensions as packages

`tau install` accepts PyPI names, pip-compatible Git URLs, local distributions, and
direct wheel or source-archive URLs:

```bash
tau install pypi:tau-tools==1.2.3
tau install git+ssh://git@github.com/example/tau-tools.git@v1
tau install ./dist/tau_tools-1.2.3-py3-none-any.whl
tau install https://packages.example.com/tau_tools-1.2.3-py3-none-any.whl
tau install tau-tools==1.2.3 --index-url https://packages.example.com/simple
```

Repeat `--extra-index-url URL` to resolve dependencies from additional indexes; index
configuration is retained for future updates. Packages install into `~/.tau/venv` and
can bundle extensions, skills, prompts, and themes through the same manifest key:

```json
{
  "tau": {
    "extensions": ["extensions/main.py"],
    "skills": ["skills"],
    "prompts": ["prompts"],
    "themes": ["themes"]
  }
}
```

Conventional resource directories are discovered when a manifest field is omitted.
Package entries in `settings.json` support an `enabled` switch plus optional
`extensions`, `skills`, `prompts`, and `themes` path filters; an empty filter disables
that resource type. Use `tau update --all` to update Tau and every installed package.

Package-sourced extensions carry the `package` source label, so they are exempt from
same-identity priority resolution and are never suppressed by a builtin of the same
name.

## The register() Entry Point

Every extension must expose a module-level callable named `register`:

```python
# .tau/extensions/my_ext.py

def register(tau):
    tau.append_prompt("Prefer standard-library solutions.")
```

If the module has no such callable, the load fails with
`No 'register(tau)' function in <file>` and is reported as an `ExtensionError`.

`register` may be `async` — Tau awaits it before continuing startup, so async
initialisation completes before tools, commands, and the system prompt are applied:

```python
async def register(tau):
    result = await tau.exec("git", ["log", "--oneline", "-5"])
    if result.code == 0:
        tau.append_prompt(f"Recent commits:\n{result.stdout}")
```

Top-level module code runs on a worker thread (so a slow third-party import does not
freeze the TUI), while `register` itself runs on the event loop thread. Keep top-level
code to `def`/`class` statements and imports; do everything else inside `register`.

Do not start long-lived resources — subprocesses, sockets, watchers, timers — directly
in `register`. Defer them to `runtime_ready` or `session_start`, and release them from
`extension_unload` and `runtime_stop`. The `sandbox`, `lsp`, and `peer` examples all
follow this discipline.

### Inline extension factories

Python API users can supply the same registration function directly, without creating a
file:

```python
from pathlib import Path

from tau.extensions import ExtensionAPI
from tau.runtime.types import RuntimeConfig


def configure(tau: ExtensionAPI) -> None:
    tau.register_tool(MyTool())
    tau.append_prompt("Use the embedding application's workflow.")


config = RuntimeConfig(
    cwd=Path.cwd(),
    extension_factories=[configure],
)
```

Inline factories receive the normal `ExtensionAPI`, may be sync or async, and load after
file-based extensions. They participate in every registration system, and failures are
isolated and reported as `ExtensionError`. `/reload` unloads the old inline extensions
and re-executes the factories, so hook subscriptions are never duplicated. Session
replacement reuses the active extension runtime and does not re-run factories.

Single-extension reload does not apply to inline factories — changing one falls back to
a full reload.

## Custom Tools

Tools are subclasses of `tau.tool.types.Tool` with a Pydantic parameter schema. See
[Creating Tools](creating-tools.md) for the full walkthrough and testing guidance.

```python
from pathlib import Path

from pydantic import BaseModel, Field
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


class CountLocSchema(BaseModel):
    path: str = Field(..., description="File path to analyse")


class CountLocTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="count_loc",
            description="Count lines of code in a file.",
            schema=CountLocSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        path = Path(invocation.params["path"])
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            return ToolResult.error(invocation.id, str(exc))
        return ToolResult.ok(
            invocation.id,
            f"{path}: {lines} lines",
            metadata={"path": str(path), "line_count": lines},
        )


def register(tau):
    tau.register_tool(CountLocTool())
```

`Tool.__init__` accepts:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Name used in LLM tool calls |
| `description` | `str` | — | Description shown to the model |
| `schema` | `type[BaseModel]` | — | Pydantic model for the arguments |
| `kind` | `ToolKind` | — | Semantic category driving execution policy |
| `execution_mode` | `ToolExecutionMode` | `Sequential` | Concurrency policy |
| `render_call` | `Callable[[dict, bool], list[str]] \| None` | `None` | Custom rendering of the tool call line |
| `render_result` | `Callable[[str, ToolRenderOptions], list[str]] \| None` | `None` | Custom rendering of the result block |
| `render_shell` | `str` | `"self"` | `"self"` for raw renderer output, `"default"` for the standard `└` framing |
| `result_expandable` | `bool` | `True` | Whether the result can be collapsed |
| `result_preview_lines` | `int \| None` | `None` | Overrides the default preview threshold |
| `prompt_snippet` | `str \| None` | `None` | One-line description injected into the system prompt |
| `prompt_guidelines` | `str \| None` | `None` | Extra system-prompt guidance for this tool |
| `prepare_arguments` | `Callable[[dict], dict] \| None` | `None` | Normalises arguments before validation |

`ToolKind` values:

| Value | Meaning |
|---|---|
| `ToolKind.Read` | Reads files or external data |
| `ToolKind.Edit` | Modifies existing files |
| `ToolKind.Write` | Creates or overwrites files |
| `ToolKind.Execute` | Runs shell commands or processes |
| `ToolKind.Web` | Network requests |

`ToolExecutionMode` values:

| Value | Meaning |
|---|---|
| `ToolExecutionMode.Sequential` | Run one at a time (default) |
| `ToolExecutionMode.Parallel` | Run concurrently with other parallel tools |
| `ToolExecutionMode.Batch` | Group with other batch tools, then run together |

`ToolResult` constructors:

```python
ToolResult.ok(invocation.id, "content", metadata={"key": "value"})
ToolResult.error(invocation.id, "error message")
ToolResult.with_images(invocation.id, "content", images=[png_bytes])
ToolResult.with_audio(invocation.id, "content", audio=[wav_bytes])
ToolResult.with_video(invocation.id, "content", video=[mp4_bytes])
```

Record in `metadata` the inputs that shaped the operation plus output statistics —
counts, sizes, flags — not content already present in the text result. Setting
`terminate=True` on the returned `ToolResult` stops the agent loop after this call.

`ToolContext` is injected by the engine:

| Attribute | Type | Description |
|---|---|---|
| `context.llm` | `LLM \| None` | Live LLM handle — call models from inside a tool |
| `context.cwd` | `Path \| None` | Working directory |
| `context.settings` | `SettingsManager \| None` | Settings access |

### Wrapping a built-in tool

`tau.get_builtin_tool(name)` returns a fresh instance of a built-in so you can delegate
execution while replacing its rendering. Supported names: `read`, `write`, `edit`,
`terminal`, `glob`, `grep`, `ls`. It returns `None` for anything else.

```python
def register(tau):
    original = tau.get_builtin_tool("read")

    def render_result(content, opts):
        lines = content.splitlines()
        return [f"{len(lines)} lines" + (" [error]" if opts.is_error else "")]

    tau.register_tool(
        Tool(
            name="read",
            description=original.description,
            schema=original.schema,
            kind=original.kind,
            render_result=render_result,
            execute=original.execute,
        )
    )
```

Extension tools may shadow built-ins while loaded. Tau retains the shadowed source
layer, so disabling or reloading the extension restores the previous implementation.
Among extensions, the last one to register a given tool name wins.

## Slash Commands

```python
async def cmd_hello(ctx, args):
    name = args[0] if args else "world"
    if ctx.ui:
        ctx.ui.notify(f"Hello, {name}! cwd={ctx.cwd}")


def register(tau):
    tau.register_command("hello", "Say hello", cmd_hello, aliases=["hi"])
```

The handler receives `(ctx: ExtensionContext, args: list[str])` and may be sync or
async.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Command name, invoked as `/name` |
| `description` | `str` | — | Shown in the command picker |
| `handler` | `Callable[[ctx, list[str]], Awaitable \| None]` | — | The implementation |
| `aliases` | `list[str] \| None` | `None` | Additional trigger words |
| `get_argument_completions` | `Callable[[str], list[AutocompleteItem]] \| None` | `None` | Dynamic argument completions |
| `argument_hint` | `str \| None` | `None` | Inline ghost text, e.g. `"<file> <description>"` |
| `requires_idle` | `bool` | `True` | Set `False` only for UI-only or read-only commands safe during an active turn |

Each `<token>` in `argument_hint` disappears as the user fills in that positional
argument.

`requires_idle=False` lets a command run mid-turn. Such commands must not mutate turn,
model, tool, session, or extension state — the `sandbox` and `todo` examples use it for
status readouts.

### Argument completions

`get_argument_completions(prefix)` is called as the user types after `/name `. It may be
sync or async, and returns `AutocompleteItem` objects:

```python
from tau.tui.autocomplete import AutocompleteItem


def branch_completions(prefix: str) -> list[AutocompleteItem]:
    branches = ["main", "dev", "feat/login", "fix/typo"]
    return [
        AutocompleteItem(label=b, description="git branch")
        for b in branches
        if b.startswith(prefix)
    ]


def register(tau):
    tau.register_command(
        "checkout",
        "Switch git branch",
        cmd_checkout,
        get_argument_completions=branch_completions,
        argument_hint="<branch>",
    )
```

## Keyboard Shortcuts

`register_shortcut` binds a literal key combination. The handler receives `(ctx,)`.

```python
def register(tau):
    @tau.register_shortcut("ctrl+g", "Open greeter")
    async def on_ctrl_g(ctx):
        if ctx.ui:
            await ctx.ui.select("Greeter", ["Hello", "Goodbye"])
```

Direct-call form:

```python
tau.register_shortcut("ctrl+g", "Open greeter", my_handler)
```

Tau compares extension shortcuts against the effective `KeyMap`. Safety-critical editor
and application bindings are reserved and cannot be replaced. A non-reserved global
binding may be replaced, with a warning. If two extensions register the same key, the
last one loaded wins and Tau reports a warning.

Low-level editor operations that are not exposed as `KeyMap` actions — cursor movement,
for instance — are still consumed directly by the focused editor, and registering a
shortcut does not override them. See
[Customising keybindings](keybindings.md#customising-keybindings).

## Event Hooks

Subscribe with `tau.on`. Handlers always receive `(event, ctx)`: the typed event
dataclass, and a fresh `ExtensionContext` snapshot built from the live runtime at
dispatch time.

```python
def register(tau):
    @tau.on("session_start")
    async def on_start(event, ctx):
        print(f"Session started — reason: {event.reason}")

    # Direct-call form
    tau.on("agent_end", lambda event, ctx: print(event.reason))
```

### Dispatch model

Tau's hook bus supports two registration styles, and the difference determines whether
your return value does anything.

1. **Observer dispatch (the default).** `ExtensionRuntime` subscribes to the bus as a
   catch-all listener and re-dispatches every event to matching extension handlers.
   Subscriber return values are discarded by the bus, so for these events **whatever you
   return is ignored**.
2. **Interceptable dispatch.** For a fixed allowlist of events, extension handlers are
   registered *directly* on the bus so their return values are collected by `emit()` and
   inspected by the caller.

Only these eight events are interceptable from an extension:

| Event | Result type | What the return value does |
|---|---|---|
| `context` | `ContextEventResult` | `ephemeral_messages` from **every** handler are accumulated |
| `tool_result` | `ToolResultEventResult` | First result wins; overrides content, error flag, metadata, or terminates the loop |
| `input` | `InputEventResult` | First `action="transform"` with `text` replaces the prompt |
| `user_terminal` | `UserTerminalResult` | First `handled=True` short-circuits the shell entirely |
| `before_compaction` | `BeforeCompactionResult` | `cancel` aborts; a supplied `compaction` replaces the LLM summary |
| `session_before_tree` | `SessionBeforeTreeResult` | Accumulates overrides; `cancel` aborts navigation |
| `resources_discover` | `ResourcesDiscoverResult` | Paths from every handler are added |
| `project_trust` | `ProjectTrustResult` | First non-`None` `trusted` decides |

> **Important:** `session_before_switch` and `session_before_fork` define a
> `cancel` field and the runtime honours it — but they are *not* in the interceptable
> allowlist, so a value returned from an extension handler is discarded and the
> operation proceeds. Only handlers registered directly on the `Hooks` bus from the
> Python API can cancel those. Treat both as observe-only from an extension.

Other dispatch properties:

- Handlers run **sequentially** in registration order, never concurrently.
- A handler that raises is caught, logged, and recorded as an `ExtensionError`; the next
  handler still runs, and the raising handler contributes no result.
- Sync and async handlers are both supported.
- Shutdown-path events are bounded by a timeout so one hung handler cannot wedge exit:
  `tui_exit` gets 2 seconds, `runtime_stop` and the per-extension reload events get 10.
- Hooks are Python-only. There is no `settings.json` schema for declaring hooks.

### Lifecycle overview

```text
tau starts
  ├─► runtime_start                (core subscribers only — extensions not loaded yet)
  ├─► (resources discovered: skills, prompts, themes)
  ├─► resources_discover           (during reload-time discovery)
  ├─► (extensions loaded: register(tau) runs for each)
  ├─► session_start { reason: startup }
  └─► runtime_ready                (everything wired; safe to start background work)
      │
      ├─► tui_ready                (interactive mode only — ctx.ui first becomes safe)
      └─► tui_start
          │
user submits a prompt ─────────────────────────────────────┐
  ├─► input (can transform the text)                       │
  ├─► agent_start                                          │
  │                                                        │
  │   ┌─── per turn ──────────────────────────────┐        │
  │   ├─► turn_start                              │        │
  │   ├─► context (can inject ephemeral messages) │        │
  │   ├─► message_start                           │        │
  │   ├─► before_provider_request (headers mutable)        │
  │   ├─► message_update … (streaming)            │        │
  │   ├─► after_provider_response                 │        │
  │   ├─► message_end                             │        │
  │   │                                           │        │
  │   │   for each tool call:                     │        │
  │   │     ├─► tool_execution_start              │        │
  │   │     ├─► tool_execution_update …           │        │
  │   │     ├─► tool_execution_end                │        │
  │   │     └─► tool_result (can rewrite/terminate)        │
  │   │                                           │        │
  │   └─► turn_end                                │        │
  │                                                        │
  ├─► agent_end                                            │
  ├─► save_point                                           │
  └─► settled                                              │
                                                           │
user submits another prompt ◄──────────────────────────────┘

/new or /resume
  ├─► session_before_switch        (observe-only from extensions)
  ├─► session_shutdown
  └─► session_start { reason: new | resume }

/fork
  ├─► session_before_fork          (observe-only from extensions)
  ├─► session_shutdown
  └─► session_start { reason: fork | clone }

tree navigation
  ├─► session_before_tree          (can cancel or supply a summary)
  ├─► branch_summary_start → branch_summary_end / _failure / _cancelled
  └─► session_tree

/compact or automatic compaction
  ├─► before_compaction            (can cancel or supply the summary)
  ├─► compaction_start
  └─► compaction_end / compaction_failure / compaction_cancelled

/reload
  ├─► extension_unload             (per old extension)
  └─► extension_reloaded           (per new extension)

exit
  ├─► tui_exit                     (interactive only, in a finally block)
  ├─► session_shutdown { reason: quit }
  └─► runtime_stop
```

### Runtime and reload events

| Event | Payload | Fires when |
|---|---|---|
| `runtime_start` | — | Runtime construction begins. **Extensions cannot observe this** — it is emitted before any extension is loaded, so nothing is subscribed. It exists for core subscribers and the Python API |
| `runtime_ready` | — | Engine, agent, tools, and extensions are all wired and `session_start` has fired, before any mode-specific loop begins. Mode-independent, so this is the right place to start background work such as warming a language server |
| `runtime_stop` | — | The mode-specific loop has exited and the process is shutting down. Fires exactly once, regardless of mode. Bounded by a 10-second timeout |
| `extension_unload` | `type` only | An extension is about to be replaced by a reload. Release subprocesses, sockets, tasks, and watchers here — reload does not do it for you |
| `extension_reloaded` | `type` only | A freshly loaded extension is now wired into the live runtime. Use it to re-establish state that `runtime_ready` would have set up, since `runtime_ready` fires only once at startup |

`extension_unload` and `extension_reloaded` are dispatched directly to a single
extension rather than broadcast on the bus, and the event object carries only `type`.

### TUI events

These fire only in interactive mode.

| Event | Payload | Fires when |
|---|---|---|
| `tui_ready` | — | Hooks are subscribed and the layout is fully constructed. The earliest point at which `ctx.ui` is safe — `session_start` with reason `startup` fires before the layout exists |
| `tui_start` | — | Immediately before the TUI event loop begins. Use it for setup that needs the layout but must precede user interaction |
| `tui_exit` | — | The TUI is shutting down, from a `finally` block, so it fires even on error. Use it for cleanup that needs `ctx.ui`; `session_shutdown` runs later, without UI. Bounded by a 2-second timeout |

The `todo` example uses `tui_ready` to paint its board widget on startup.

### Session events

| Event | Payload | Result | Notes |
|---|---|---|---|
| `session_start` | `reason`, `previous_session_file` | — | `reason`: `startup`, `reload`, `new`, `resume`, `fork`, `clone` |
| `session_shutdown` | `reason`, `target_session_file` | — | `reason`: `quit`, `reload`, `new`, `resume`, `fork`, `clone`. Only fires on session transitions and quit — not on process exit in every mode; pair it with `runtime_stop` |
| `session_before_switch` | `reason`, `target_session_file` | `SessionBeforeSwitchResult` (observe-only from extensions) | `reason`: `new`, `resume` |
| `session_before_fork` | `entry_id`, `position` | `SessionBeforeForkResult` (observe-only from extensions) | `position`: `before`, `at` |
| `session_before_tree` | `preparation` | `SessionBeforeTreeResult` | Interceptable. `preparation` is a `TreePreparation` and may be mutated in place |
| `session_tree` | `new_leaf_id`, `old_leaf_id`, `from_extension` | — | The tree has been rewritten |
| `branch_summary_start` | `old_leaf_id`, `target_id`, `from_extension` | — | Summary generation begins |
| `branch_summary_end` | `old_leaf_id`, `target_id`, `summary_entry_id`, `summary_length`, `from_extension` | — | Summary attached to the destination |
| `branch_summary_failure` | `old_leaf_id`, `target_id`, `error` | — | Summary failed; navigation continues without it |
| `branch_summary_cancelled` | `old_leaf_id`, `target_id`, `reason` | — | Summary or navigation was cancelled |

`TreePreparation` fields: `target_id`, `old_leaf_id`, `common_ancestor_id`,
`entries_to_summarize`, `custom_instructions`, `replace_instructions`, `label`.

`SessionBeforeTreeResult` fields: `cancel`, `custom_instructions`,
`replace_instructions`, `label`, `summary`, `summary_details`. Overrides accumulate
across handlers; `summary` is first-wins.

```python
from tau.hooks import SessionBeforeTreeResult


def register(tau):
    @tau.on("session_before_tree")
    async def summarize(event, ctx):
        entries = event.preparation.entries_to_summarize
        return SessionBeforeTreeResult(
            summary=await my_summariser(entries),
            summary_details={"source": "my-extension"},
        )
```

### Agent and turn events

| Event | Payload | Notes |
|---|---|---|
| `agent_start` | — | The engine loop begins |
| `agent_end` | `messages`, `reason` | `reason`: `completed`, `aborted`, `error` |
| `agent_error` | `error` | The loop terminated with an unrecoverable error |
| `turn_start` | `turn_index`, `timestamp` | Both fields are currently always `0` — they are never populated at the emit site. Track turn counts yourself if you need them |
| `turn_end` | `turn_index`, `message`, `tool_results` | `turn_index` is likewise always `0` |
| `settled` | — | The invocation drained its queues and post-run compaction with nothing queued. An observation, not a lock against concurrent submissions |
| `save_point` | — | Session writes are flushed; on-disk state is consistent |

### Message events

| Event | Payload | Notes |
|---|---|---|
| `message_start` | `message` | The model begins streaming a message |
| `message_update` | `message` | One incremental streaming chunk |
| `message_end` | `message` | The message is complete. **Return values are ignored** — `MessageEndEventResult` exists but nothing consumes it |
| `message_rollback` | `count` | The last `count` committed messages are being retracted. Emitted when an interrupted tool turn is discarded, so the persisted assistant tool-call message and its result must be removed to keep history consistent |

### Tool events

| Event | Payload | Result | Notes |
|---|---|---|---|
| `tool_execution_start` | `tool_call` (`ToolCallContent`) | — | Just before `execute()` runs |
| `tool_execution_update` | `partial_tool_result` (`ToolResult`) | — | One streaming progress update |
| `tool_execution_end` | `tool_result` (`ToolResultContent`) | — | `execute()` returned |
| `tool_execution_failure` | `tool_name`, `tool_call_id`, `input`, `error` | — | The tool raised an uncaught exception, as distinct from returning an error result |
| `tool_result` | `tool_call_id`, `tool_name`, `input`, `content`, `is_error` | `ToolResultEventResult` | Interceptable — the only tool hook whose return value is honoured |

`ToolResultEventResult` fields:

| Field | Type | Effect |
|---|---|---|
| `content` | `str \| None` | Replaces the result text |
| `is_error` | `bool \| None` | Overrides the error flag |
| `terminate` | `bool` | Ends the agent loop after this result |
| `metadata` | `dict \| None` | Merged into the result's metadata |

The first `ToolResultEventResult` returned wins; later handlers are not consulted.

```python
from tau.hooks import ToolResultEventResult

SECRET = "sk-live-"


def register(tau):
    @tau.on("tool_result")
    async def redact(event, ctx):
        if SECRET in event.content:
            return ToolResultEventResult(
                content=event.content.replace(SECRET, "sk-live-[redacted]"),
                metadata={"redacted": True},
            )
        return None
```

### Input and terminal events

| Event | Payload | Result | Notes |
|---|---|---|---|
| `input` | `text`, `source` | `InputEventResult` | Interceptable. `source` is currently always `"interactive"` at the sole emit site |
| `user_terminal` | `command`, `private`, `cwd` | `UserTerminalResult` | Interceptable, fires before the shell runs |
| `terminal_execution` | `message`, `streaming` | — | Fires at start (`streaming=True`) and completion (`streaming=False`) |
| `terminal_output` | `message` | — | One output chunk from a running `!` command |

`InputEventResult` declares `action: "continue" | "transform" | "handled"` and `text`.
Only `action="transform"` with a non-`None` `text` is honoured — **`"handled"` is
declared but not implemented**, and the prompt still reaches the agent.

### Provider events

| Event | Payload | Notes |
|---|---|---|
| `before_provider_request` | `model`, `provider_id`, `messages`, `headers`, `options` | `headers` is the live dict sent as `extra_headers` on this request; mutating it in place takes effect immediately |
| `after_provider_response` | `model`, `response`, `status_code`, `response_headers` | Raw HTTP status and headers are captured before the stream body is consumed, for providers that report them (Anthropic Messages and the OpenAI Completions/Responses APIs). `None` otherwise |

Return values are discarded for both. Mutate `headers` instead:

```python
def register(tau):
    @tau.on("before_provider_request")
    async def add_trace_header(event, ctx):
        event.headers["X-Trace-Id"] = new_trace_id()
```

### Compaction events

| Event | Payload | Result | Notes |
|---|---|---|---|
| `before_compaction` | `preparation`, `entries`, `manual`, `reason`, `will_retry` | `BeforeCompactionResult` | Interceptable |
| `compaction_start` | `manual`, `reason`, `will_retry` | — | Compaction begins after interception |
| `compaction_end` | `manual`, `tokens_before`, `summary_length`, `from_extension`, `reason`, `will_retry` | — | Compaction succeeded |
| `compaction_failure` | `manual`, `reason`, `will_retry`, `error` | — | Compaction failed |
| `compaction_cancelled` | `manual`, `reason`, `will_retry` | — | An extension cancelled compaction |

`reason` is a `CompactionReason`: `manual`, `threshold`, or `overflow`. `will_retry`
indicates whether the aborted turn is retried after compaction (overflow recovery).

### Model, queue, and persistence events

| Event | Payload | Notes |
|---|---|---|
| `model_select` | `model`, `previous_model`, `source` | `source` is declared as `set`, `cycle`, or `restore`, but only `"set"` is currently emitted |
| `thinking_level_select` | `level`, `previous_level` | Emitted by the `/model` command and by `tau.set_thinking_level()` |
| `queue_update` | `queue`, `message`, `messages` | `queue` is `"steering"` or `"followup"` |

### Reserved events that never fire

The following event types are defined and exported but have **no emit site** anywhere in
Tau. Do not subscribe to them — a handler will never run.

| Event | Result type | Status |
|---|---|---|
| `tool_call` | `ToolCallEventResult` | Never emitted. The equivalent interception point is the engine's non-hook `before_tool_call` callback, which is not exposed to extensions. Use `tool_result` to rewrite an outcome, or shadow the tool itself to gate it |
| `before_agent_start` | `BeforeAgentStartEventResult` | Never emitted. Use `tau.append_prompt()` for static prompt additions, or `context` for per-request injection |

Two result types are also declared but never consumed:
`MessageEndEventResult` (returned from `message_end`), and
`ContextEventResult.messages` — only the `ephemeral_messages` field of a
`ContextEventResult` is read.

## Interception Recipes

### Custom compaction

Return a `BeforeCompactionResult` from `before_compaction` to cancel compaction or
replace Tau's default LLM summarisation.

| Return value | Effect |
|---|---|
| `BeforeCompactionResult(cancel=True)` | Emits `compaction_cancelled` and aborts. A manual `/compact` surfaces the cancellation as an error |
| `BeforeCompactionResult(compaction=<CompactionResult>)` | Uses your summary instead of calling the LLM; `compaction_end` reports `from_extension=True` |
| `None` | Falls through to the default algorithm |

Handlers run in registration order; the first non-`None` result wins. If a handler
raises it is logged and the next runs; if every handler falls through, the default
algorithm runs.

`event.preparation` is a `CompactionPreparation`:

| Field | Description |
|---|---|
| `messages_to_summarize` | Messages that the summary replaces |
| `turn_prefix_messages` | Messages from a split turn that also need summarising |
| `first_kept_entry_id` | ID of the first session entry kept verbatim after the summary |
| `tokens_before` | Estimated token count before compaction |
| `is_split_turn` | Whether compaction cuts inside an in-progress turn |
| `settings` | The `CompactionSettings` in effect for this run |
| `previous_summary` | Summary text from a prior compaction cycle, if any |

`CompactionResult` requires `summary`, `first_kept_entry_id`, and `tokens_before`, and
optionally accepts `details`.

`event.entries` is the full raw session entry list for the current branch.

Summarise with your own model call:

```python
from tau.hooks import BeforeCompactionResult
from tau.session.compaction import CompactionResult


def register(tau):
    @tau.on("before_compaction")
    async def handle(event, ctx):
        preparation = event.preparation
        if preparation is None:
            return None

        lines = []
        for msg in preparation.messages_to_summarize:
            text = getattr(msg, "text", "") or ""
            if text:
                lines.append(f"{getattr(msg, 'role', 'unknown')}: {text[:200]}")

        summary = await my_summariser("\n".join(lines))
        if not summary:
            return None

        return BeforeCompactionResult(
            compaction=CompactionResult(
                summary=summary,
                first_kept_entry_id=preparation.first_kept_entry_id,
                tokens_before=preparation.tokens_before,
            )
        )
```

Block automatic compaction while allowing manual `/compact`:

```python
from tau.hooks import BeforeCompactionResult


def register(tau):
    @tau.on("before_compaction")
    def block_auto(event, ctx):
        if not event.manual:
            return BeforeCompactionResult(cancel=True)
        return None
```

### Ephemeral context injection

For state that must be fresh on every LLM request but must never be persisted — browser
screenshots, computer-use state, live telemetry — return `ephemeral_messages` from a
`context` handler:

```python
from tau.hooks import ContextEventResult
from tau.message.types import UserMessage


def register(tau):
    @tau.on("context")
    async def inject_state(event, ctx):
        screenshot = await capture_screen()
        return ContextEventResult(
            ephemeral_messages=[
                UserMessage.with_images("Current browser state", images=[screenshot])
            ]
        )
```

The hook runs before every inference, including after tool execution.
`ephemeral_messages` must contain only `UserMessage` objects. They are appended to the
provider request only, are not persisted, and are excluded from Anthropic prompt-cache
breakpoints. Every handler's messages are accumulated — there is no short-circuit.

`ContextEventResult.messages` exists in the dataclass but is not read; there is
currently no way for an extension to rewrite the stable request context.

### Rewriting user input

```python
from tau.hooks import InputEventResult

MACROS = {"!ship": "Run the tests, then commit and push."}


def register(tau):
    @tau.on("input")
    async def expand_macros(event, ctx):
        if event.text.strip() in MACROS:
            return InputEventResult(action="transform", text=MACROS[event.text.strip()])
        return None
```

### Intercepting shell commands

`user_terminal` fires before Tau runs a shell command on the user's behalf. Returning
`UserTerminalResult(handled=True, ...)` short-circuits execution entirely — the shell
never runs, and your output is recorded as the command's result.

```python
from tau.hooks import UserTerminalResult


def register(tau):
    @tau.on("user_terminal")
    async def block_destructive(event, ctx):
        if "rm -rf /" in event.command:
            return UserTerminalResult(
                handled=True,
                output="Blocked by policy extension.",
                exit_code=1,
            )
        return None
```

### Contributing resource paths

`resources_discover` lets an extension add skill, prompt, and theme directories. Paths
from every handler are collected additively.

```python
from pathlib import Path

from tau.hooks import ResourcesDiscoverResult


def register(tau):
    @tau.on("resources_discover")
    async def add_paths(event, ctx):
        root = Path(event.cwd) / "team-resources"
        return ResourcesDiscoverResult(
            skill_paths=[str(root / "skills")],
            prompt_paths=[str(root / "prompts")],
            theme_paths=[str(root / "themes")],
        )
```

For skills that ship *inside* your extension directory, prefer the manifest `"skills"`
field instead — it registers them synchronously right after discovery, whereas a
`resources_discover` handler registered during `register(tau)` is always one reload
generation late.

### Deciding project trust

`project_trust` is emitted when `ctx.is_project_trusted()` is called. The first handler
returning a non-`None` `trusted` decides.

```python
from tau.hooks import ProjectTrustResult


def register(tau):
    @tau.on("project_trust")
    async def auto_trust(event, ctx):
        if "/my-safe-org/" in event.project_dir:
            return ProjectTrustResult(trusted=True)
        return None  # fall through to the default resolution
```

`ProjectTrustResult.remember` is declared but not currently read; persist a decision
explicitly with `ctx.set_project_trusted(True, remember=True)`, which writes to
`~/.tau/trust.json`.

## TUI: Widgets, Dialogs, and Overlays

`ctx.ui` is a `UIContext` inside a TUI session. In RPC mode it is an `RpcUIContext`,
which speaks the same API over the JSON-lines protocol. It is `None` only when there is
no user-facing surface at all (print/JSON modes). Always guard on it.

| Capability | TUI | RPC | Check |
|------------|-----|-----|-------|
| Dialogs — `select`, `confirm`, `prompt`, `editor` | ✅ | ✅ | `ctx.has_ui` |
| `notify`, `set_status`, `set_widget` (lines), `set_title`, `set_editor_text` | ✅ | ✅ | `ctx.has_ui` |
| `multi_select` — pick several | ✅ | ✅ | `ctx.has_ui` |
| Components — `custom`, `custom_inline`, `show_overlay`, footers, headers, themes | ✅ | ❌ no-op | **`ctx.ui.supports_components`** |

`ctx.has_ui` is defined as `ctx.ui is not None` — use whichever reads better, they are the
same test. There is only one real branch point beyond it: `supports_components`.

Rendering your own `Component` is the case that needs the capability flag:

```python
ui = ctx.ui
if ui is None or not ui.supports_components:
    return          # no grid to draw on — custom_inline() would return None
await ui.custom_inline(my_factory)
```

`ctx.has_ui` answers the narrower question "can I ask the user something at all" — it is
`True` in RPC mode, because dialogs do work there.

In a TUI the earliest event at which `ctx.ui` is reliably available is `tui_ready`;
`session_start` with reason `startup` fires before the layout exists. In RPC mode the
bridge is installed before `session_start`, so `ctx.ui` is usable from the first handler.

```python
def register(tau):
    @tau.on("tui_ready")
    async def on_ready(event, ctx):
        if ctx.ui is None:
            return
        ctx.ui.set_status("my-ext", "● connected")
```

### Widgets above and below the editor

```python
ctx.ui.set_widget("banner", ["  Uncommitted changes  "], placement="above_editor")
ctx.ui.remove_widget("banner")
```

`placement` is `"above_editor"` (default) or `"below_editor"`. The `id` is how you
update or remove the widget later. Pass a list of strings for static text, or a
`Component` for anything interactive or dynamic. The `todo` example implements a full
task board this way, driving a custom `Component` and calling `ctx.ui.request_render()`
when its content changes.

### Status slots and footer

```python
ctx.ui.set_status("git", "main ↑2")   # add or update a named footer slot
ctx.ui.clear_status("git")            # remove it
```

Slots are keyed by id, so multiple extensions coexist without interfering.

To replace the footer entirely, pass a component or a factory. Tau detects the factory's
arity automatically:

```python
from tau.modes.interactive.ui_context import FooterData

def my_footer(tui, theme, data: FooterData):
    branch = data.git_branch or "detached"
    pct = f"{data.context_percent:.0f}%" if data.context_percent is not None else "—"
    return StaticComponent([f"  {branch}  {data.model_id}  ctx:{pct}  "])

ctx.ui.set_footer(my_footer)
ctx.ui.restore_footer()
```

Supported factory signatures: `factory()`, `factory(tui, theme)`, and
`factory(tui, theme, footer_data)`.

`FooterData` fields:

| Field | Type | Description |
|---|---|---|
| `git_branch` | `str` | Current branch name, or `""` |
| `context_tokens` | `int \| None` | Estimated tokens used so far |
| `context_window` | `int` | The model's total context window |
| `context_percent` | `float \| None` | `context_tokens / context_window * 100` |
| `active_extensions` | `list[str]` | Names of loaded extensions |
| `model_id` | `str` | Active model identifier |
| `provider_id` | `str` | Active provider identifier |

### Dialogs

All dialog methods are `async` and return `None` (or `False`) when the user cancels or
when running headless.

```python
choice = await ctx.ui.select("Pick an action", ["Summarize", "Explain", "Translate"])
ok     = await ctx.ui.confirm("Delete branch?", "This cannot be undone.")
key    = await ctx.ui.prompt("Enter API key", secret=True)
text   = await ctx.ui.editor("System prompt", prefill=ctx.get_system_prompt())
```

`ctx.ui.editor` opens a floating multi-line editor. `Ctrl+S` (or `Ctrl+Enter`) saves and
closes; `Escape` cancels. Arrow keys, `Home`/`End`, `Backspace`, and `Enter` all behave
normally.

`ExtensionContext` also exposes `await ctx.select(...)` and `await ctx.confirm(...)` as
convenience shims that work without touching `ctx.ui`, returning `None`/`False`
headless.

The `loop` example composes `select`, `prompt`, and `confirm` into a complete
interactive manager driven by a single slash command.

### Custom overlays and components

For anything the built-in dialogs cannot express, render your own `Component`.

`ctx.ui.custom(factory, options=None)` shows a focusable component as a floating overlay
and awaits its result. The factory is called with `(tui, theme, keybindings, done)`;
call `done(value)` from inside the component to resolve and close it.

```python
from tau.tui.component import Component
from tau.tui.service import CustomOptions, OverlayOptions


class CounterComponent(Component):
    def __init__(self, done):
        self._count = 0
        self._done = done

    def render_cells(self, area, buf):
        buf.grow_to(area.y + 1)
        buf.set_string(area.x, area.y, f"  Count: {self._count}  (Enter to confirm)")
        return 1

    def handle_input(self, event):
        if event.matches("enter"):
            self._done(self._count)
            return True
        if event.matches("escape"):
            self._done(None)
            return True
        if event.matches("up"):
            self._count += 1
            return True
        return False


async def cmd_count(ctx, args):
    if ctx.ui is None:
        return
    result = await ctx.ui.custom(
        lambda tui, theme, kb, done: CounterComponent(done),
        CustomOptions(overlay_options=OverlayOptions(width="40%", anchor="center")),
    )
    ctx.ui.notify(f"Result: {result}")
```

`ctx.ui.custom_inline(factory, kind="custom")` renders the component *inline*, replacing
the input editor between the divider lines the way `/settings` does, rather than
floating over the screen. It avoids a separate compositing pass, so it cannot be skipped
by the scrollback renderer's frozen-content optimisation. The component must own its own
navigation, commit, and cancel handling.

`ctx.ui.show_overlay(component, width="60%", max_height="80%", anchor="center",
non_capturing=False)` shows a non-awaited overlay and returns a handle with `.close()`:

```python
from tau.modes.interactive.components.overlays import TextOverlay

handle = ctx.ui.show_overlay(
    TextOverlay(["Hello!", "Press Esc to close"]),
    width="50%",
    anchor="top-right",
    non_capturing=True,
)
handle.close()
```

The `ask_user` example is a complete worked implementation of a custom interactive
dialog component. It shows the patterns worth copying for any multi-step dialog:

- **One question** renders bare. **Several** get a tab bar plus a review step, so the
  user can move between them with `←`/`→`, revise an earlier answer, and submit once —
  `_AskUserSequence` owns the tabs and delegates everything else to a per-question
  child component.
- A child in text-entry mode keeps every key (`is_editing`), so the wrapper never
  steals the arrows that move the text cursor.
- On a multi-select question, `Space` on the `Type something…` row opens an editor
  whose `Enter` **saves** rather than submits — the answer is then the ticked options
  *and* the typed text. Saving an empty string clears it again.
- **Two backends, one answer shape.** In a TUI it renders the component above. Under
  RPC — where `ctx.ui.supports_components` is `False` — `rpc_backend.py` asks the same
  questions through the protocol's fixed dialogs (`select`, `multi_select`,
  `input`/`editor`), folding option descriptions into the labels. The model gets
  identical answers either way; what is lost is the tabs, the review step and previews.
- When there is no UI at all (print/JSON mode, `ctx.ui is None`) the tool removes itself
  from `engine.tools` and says so in the error, instead of failing identically on every
  subsequent turn. `ExtensionAPI.set_active_tools` does the same for a whole allowlist.
- `timeout` is an *inactivity* timer: every keystroke re-arms it, and firing it tears
  the dialog down as well as failing the call.

### Other UI surfaces

```python
ctx.ui.notify("Operation complete")                       # inline notification
ctx.ui.clear_messages()                                   # clear the transcript
ctx.ui.set_header(StaticComponent(["── My Extension ──"])) # banner above messages
ctx.ui.set_title("My Agent – session 42")                 # terminal window title

ctx.ui.set_working_message("Fetching data…")              # spinner label
ctx.ui.set_working_visible(False)                         # hide the spinner
ctx.ui.set_working_indicator(["◐", "◓", "◑", "◒"])        # custom animation
ctx.ui.set_hidden_thinking_label("reasoning…")            # collapsed-thinking label

ctx.ui.get_editor_text()                                  # read the input editor
ctx.ui.set_editor_text("Write a poem")                    # replace its content
ctx.ui.paste_to_editor("@file.py ")                       # insert at the cursor
ctx.ui.set_editor_component(factory)                      # swap the editor entirely
ctx.ui.set_editor_component(None)                         # restore the default

ctx.ui.get_all_themes()                                   # list[str]
ctx.ui.set_theme("dark", persist=False)                   # True on success
ctx.ui.theme                                              # active LayoutTheme

ctx.ui.set_tools_expanded(False)                          # collapse tool-call blocks
ctx.ui.set_tool_results_expanded(True)                    # expand tool results
ctx.ui.has_active_selector()                              # a modal holds focus
ctx.ui.request_render()                                   # repaint now

unsub = ctx.ui.on_terminal_input(on_key)                  # raw key subscription
unsub()
```

## Custom Message Renderers

Register a renderer for a custom message type and it is used whenever the message list
encounters a `CustomMessage` with a matching `custom_type`.

```python
from tau.tui.style import apply_style


def render_banner(message, theme, width):
    return [apply_style(theme.accent, message.contents[0].content)]


def register(tau):
    tau.register_message_renderer("banner", render_banner)
```

The renderer signature is `renderer(message, theme, width) -> list[str]`. Style text
through the theme's semantic roles — `theme.muted`, `theme.accent`, `theme.success`,
`theme.warning`, `theme.error` — each a `Style` applied with
`tau.tui.style.apply_style`. Do not import ANSI codes from internal modules; those are
not a stable surface for extensions loaded from `~/.tau`.

Renderers are replaced wholesale on reload; the last extension to register a given type
wins.

## Editor Autocomplete Providers

A provider is activated by a single trigger character typed in the editor. As the user
continues typing, the dropdown filters.

```python
from tau.tui.autocomplete import AutocompleteItem


def register(tau):
    async def issue_items(ctx):
        # ctx.trigger == "#", ctx.query == text typed after "#"
        results = await search_issues(ctx.query)
        return [
            AutocompleteItem(label=f"#{i.id}", description=i.title)
            for i in results
        ]

    tau.add_autocomplete_provider("#", issue_items, description="GitHub issues")
```

`trigger` must be exactly one character; anything else raises `ValueError` at
registration time. `get_items` may be sync or async — sync providers populate the picker
immediately, async providers run in the background and the picker appears when results
arrive.

`AutocompleteItem` fields:

| Field | Type | Description |
|---|---|---|
| `label` | `str` | Displayed in the picker (required) |
| `description` | `str` | Dimmed secondary text |
| `insert_text` | `str \| None` | Text inserted into the editor; defaults to `label` |

`AutocompleteContext` fields available inside `get_items`:

| Field | Type | Description |
|---|---|---|
| `text` | `str` | Full editor text at call time |
| `cursor_pos` | `int` | Character index of the cursor |
| `trigger` | `str` | The trigger character |
| `query` | `str` | Text typed after the trigger, up to the cursor |

Keyboard behaviour while the dropdown is open:

| Key | Action |
|---|---|
| ↑ / ↓, or Ctrl+P / Ctrl+N | Navigate items |
| Tab or Enter | Accept the selection |
| Escape | Dismiss without selecting |
| Any other key | Update the filter query |

A synchronous example:

```python
EMOJI = {"smile": "😊", "fire": "🔥", "check": "✅"}


def register(tau):
    def emoji_items(ctx):
        return [
            AutocompleteItem(label=f":{name}:", description=char, insert_text=char)
            for name, char in EMOJI.items()
            if ctx.query.lower() in name
        ]

    tau.add_autocomplete_provider(":", emoji_items, description="Emoji")
```

## Themes

```python
from tau.tui.theme import LayoutTheme, SpinnerTheme


def register(tau):
    tau.register_theme(
        "ocean",
        LayoutTheme(
            primary="#0ea5e9",
            secondary="#38bdf8",
            spinner=SpinnerTheme(label_thinking="thinking…"),
        ),
    )
```

`register_theme` accepts either a `LayoutTheme` instance or a zero-argument factory that
returns one — prefer the factory for lazy loading. Registration takes effect
immediately in the global theme registry, and the theme appears in the `/theme` picker.
See [Themes](themes.md) for the full theme schema.

## Custom LLM Providers

`tau.register_provider(provider_id, config)` adds a provider and its models to the
built-in registries. It takes effect immediately: any `TextLLM` created after the call
can use it, and the models appear in `/model`.

```python
def register(tau):
    tau.register_provider(
        "my-llm",
        {
            "name": "My LLM",
            "api": "openai_completions",
            "base_url": "https://api.my-llm.com/v1",
            "api_key": "$MY_LLM_API_KEY",
            "auth_header": True,
            "models": [
                {"id": "fast-7b", "name": "My Fast 7B", "context_window": 8192},
                {"id": "smart-70b", "name": "My Smart 70B", "context_window": 32768},
            ],
        },
    )
```

Config keys:

| Key | Type | Description |
|---|---|---|
| `name` | `str` | Display name in the model picker. Defaults to `provider_id` |
| `api` | `str` | Built-in API implementation to use. Defaults to `"openai_completions"` |
| `base_url` | `str` | API endpoint URL |
| `api_key` | `str` | Literal value, `"$ENV_VAR"`, or `"!shell-command"` (stdout is the key) |
| `headers` | `dict[str, str]` | Extra HTTP headers. Values also accept `$ENV_VAR` / `!command`, resolved once and cached |
| `auth_header` | `bool` | Adds `Authorization: Bearer <api_key>` automatically |
| `stream` | `callable` | Custom transport: an async generator `(context, model, options)` yielding `LLMEvent`. Replaces `api` entirely |
| `oauth` | `dict` | Register an OAuth provider for `/login` instead of a static key |
| `models` | `list[dict]` | Model definitions |

Valid `api` values: `openai_responses`, `openai_completions`, `openai_codex_responses`,
`anthropic_messages`, `anthropic_claude_code`, `github_copilot_chat`, `gemini_generate`,
`mistral_chat`, `ollama_chat`, `google_antigravity`, `google_vertex`, `anthropic_vertex`,
`openai_vertex`, `xai`.

Model definition fields — only `id` is required:

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | — | Model identifier used in requests |
| `name` | `str` | `id` | Display name |
| `provider` | `str` | `provider_id` | Overrides the owning provider |
| `context_window` | `int` | `0` | Total context window |
| `max_input_tokens` | `int \| None` | `None` | Input cap, when the provider enforces one |
| `max_output_tokens` | `int` | `16384` | Output cap. Alias: `max_tokens` |
| `input_price` | `float` | `0.0` | Cost per input token |
| `output_price` | `float` | `0.0` | Cost per output token |
| `thinking` | `bool` | `False` | Whether the model supports extended thinking |
| `input` | `list[str]` | `["text"]` | Input modalities: `text`, `image`, `audio`, `video` |
| `output` | `list[str]` | `["text"]` | Output modalities |

The `oauth` block requires `login` (an async `(callbacks) -> OAuthCredential`) and
optionally accepts `refresh_token`, `logout`, `validate`, `get_api_key`, `name`, and
`uses_callback_server`.

`tau.unregister_provider(provider_id)` removes the provider and all of its models. It is
a no-op if the id is unknown. Built-in providers can be removed this way too, but they
return on the next process restart.

See [Inference Providers](inference-providers.md) for the provider architecture.

## Inter-Extension Services

One extension can publish an object for others to consume. Services live in a registry
shared across every extension.

```python
# provider extension
def register(tau):
    tau.provide("lsp", LSPService(cwd=tau.cwd))
```

```python
# consumer extension
def register(tau):
    @tau.on("runtime_ready")
    async def wire_up(event, ctx):
        lsp = tau.get_service("lsp")
        if lsp is None:
            return  # provider not installed or disabled — treat as optional
        await lsp.warm_up()
```

Publish from `register()` and resolve from a `runtime_ready` handler: by the time
`runtime_ready` fires, every extension has finished loading, so ordering is not a
concern. `get_service` returns `None` when nothing has registered the name — treat that
as a soft dependency rather than an error.

The registry is cleared on a full reload, and a single-extension reload evicts only that
extension's own services. Never retain a service object across reload boundaries;
re-resolve it from `extension_reloaded` or `runtime_ready`.

## Session Persistence

`tau.append_entry(custom_type, data)` writes arbitrary data into the session's JSONL log.
The entry survives restarts and is visible through `ctx.branch_entries` on the next
load. It returns the new entry's ID, or `None` if no session is active. Namespace
`custom_type` so extensions do not collide.

```python
from tau.session.types import CustomInfoEntry


def register(tau):
    @tau.on("turn_end")
    async def checkpoint(event, ctx):
        tau.append_entry("my-ext:checkpoint", {"turn": len(ctx.branch_entries)})

    @tau.on("session_start")
    async def restore(event, ctx):
        for entry in reversed(ctx.branch_entries):
            if isinstance(entry, CustomInfoEntry) and entry.custom_type == "my-ext:checkpoint":
                apply_state(entry.data)
                break
```

### branch_entries vs session_entries

A session file stores entries from every branch ever taken — forks, navigations,
abandoned paths.

| | `ctx.branch_entries` | `ctx.session_entries` |
|---|---|---|
| Contains | Root → current leaf, in order | Every entry in the file, all branches |
| Use for | Restoring per-branch extension state | Cross-branch analysis (timelines, totals) |
| Risk | — | Reads data from abandoned branches |

**Always use `branch_entries` when restoring state.** The `todo` example rebuilds its
entire task list by replaying `ctx.branch_entries`, which is why the board follows
fork and tree navigation automatically.

Related session metadata helpers:

```python
tau.set_session_name("release-triage")   # shown in the session picker
tau.get_session_name()                   # str | None
tau.set_label(entry_id, "checkpoint")    # bookmark a branch point; None clears it
```

## Configuration and Settings

`tau.config` is the `settings` dict of the matching entry in `extensions.list`, or an
empty dict when no entry exists:

```json
{
  "extensions": {
    "list": [
      {
        "path": "~/.tau/extensions/my_ext.py",
        "enabled": true,
        "settings": { "api_key": "sk-...", "verbose": true }
      }
    ]
  }
}
```

```python
def register(tau):
    if not tau.config.get("enabled", True):
        return
    api_key = tau.config.get("api_key", "")
```

For type-safe access with defaults, validation, and nested structures, wrap it in
`ExtensionSettings` with a dataclass schema:

```python
from dataclasses import dataclass

from tau.extensions import ExtensionSettings


@dataclass
class RetryConfig:
    enabled: bool = True
    max_attempts: int = 3


@dataclass
class MyConfig:
    api_key: str = ""
    timeout_ms: int = 5000
    retry: RetryConfig | None = None


def register(tau):
    config = ExtensionSettings(MyConfig, tau.config)
    timeout = config.get("timeout_ms")
    attempts = config.get_nested("retry.max_attempts", 3)
```

`ExtensionSettings` raises `ExtensionSettingsError` if the schema is not a dataclass.

To expose settings in the `/settings` panel — either declaratively via `manifest.json`
or programmatically via `tau.register_settings(...)` — and for how values are persisted
back to `settings.json`, see [Extension Settings](extension-settings.md). Extensions can
also be enabled and disabled per scope from the `/extensions` panel.

## Flags

For values that should not live in `settings.json` — tokens, machine-local switches —
declare an environment-backed flag:

```python
def register(tau):
    tau.register_flag("token", type="str", env="MY_EXT_TOKEN", default="")
    tau.register_flag("verbose", type="bool", env="MY_EXT_VERBOSE", default=False)

    token = tau.get_flag("token")
    verbose = tau.get_flag("verbose")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Flag name used with `get_flag` |
| `type` | `"bool" \| "str" \| "int"` | `"str"` | Coercion applied to the env value |
| `default` | `bool \| str \| int \| None` | `None` | Returned when the env var is unset |
| `description` | `str \| None` | `None` | Human-readable description |
| `env` | `str \| None` | `None` | Environment variable to read |

`get_flag` reads the environment at call time, so changes are picked up without a
restart. For `type="bool"`, the values `1`, `true`, and `yes` (case-insensitive) are
truthy. It returns `None` for an unregistered flag.

## Shell Execution

```python
def register(tau):
    @tau.on("runtime_ready")
    async def on_ready(event, ctx):
        result = await tau.exec("git", ["status", "--porcelain"])
        if result.code == 0 and result.stdout.strip():
            ctx.ui and ctx.ui.set_status("git", "dirty")
```

`tau.exec(cmd, args=None, cwd=None)` returns `ExecResult(stdout, stderr, code)`. `cwd`
defaults to the session working directory. Stdin is connected to `/dev/null`.

## API Reference

### ExtensionAPI

Available only inside `register(tau)`, though the runtime-backed methods below also work
from handlers that fire after startup.

**Registration**

| Method | Description |
|---|---|
| `tau.register_tool(tool)` | Add a tool the model can call |
| `tau.register_command(name, description, handler, aliases=None, get_argument_completions=None, argument_hint=None, requires_idle=True)` | Add a `/name` slash command |
| `tau.on(event_type, handler=None)` | Subscribe to a lifecycle event; usable as a decorator |
| `tau.register_shortcut(key, description=None, handler=None)` | Bind a literal key combination; usable as a decorator |
| `tau.append_prompt(text)` | Append text verbatim to the system prompt |
| `tau.register_theme(name, theme_or_factory)` | Add a named theme to the `/theme` picker |
| `tau.register_message_renderer(custom_type, renderer)` | Render a custom message type in the TUI |
| `tau.add_autocomplete_provider(trigger, get_items, description="")` | Register an editor autocomplete provider |
| `tau.register_settings(items, title="", on_change=None)` | Expose a `/settings` sub-panel |
| `tau.register_provider(provider_id, config)` | Register a custom LLM provider |
| `tau.unregister_provider(provider_id)` | Remove a provider and all its models |
| `tau.register_flag(name, type="str", default=None, description=None, env=None)` | Declare an env-backed flag |
| `tau.provide(name, service)` | Publish a service object for other extensions |

**Runtime-backed**

| Method | Returns | Description |
|---|---|---|
| `tau.get_flag(name)` | `bool \| str \| int \| None` | Read a registered flag |
| `tau.get_service(name)` | `Any \| None` | Resolve a published service |
| `tau.get_builtin_tool(name)` | `Tool \| None` | Fresh instance of `read`, `write`, `edit`, `terminal`, `glob`, `grep`, or `ls` |
| `tau.set_session_name(name)` | — | Set the session display name |
| `tau.get_session_name()` | `str \| None` | Current session display name |
| `tau.set_label(entry_id, label=None)` | — | Set or clear a label on a session entry |
| `tau.append_entry(custom_type, data=None)` | `str \| None` | Persist data into the session JSONL |
| `tau.get_commands()` | `list[dict]` | Every registered command: `{"name", "description"}` |
| `tau.get_active_tools()` | `list[str]` | Tool names currently visible to the agent |
| `tau.get_all_tools()` | `list[dict]` | Every registered tool: `{"name", "description", "parameters", "prompt_guidelines"}` |
| `tau.set_active_tools(names)` | — | Restrict the agent to those tools; an empty list restores all |
| `tau.get_thinking_level()` | `str` | Current level, `"off"` when disabled |
| `tau.set_thinking_level(level)` | — | Set the level; invalid strings are ignored |
| `tau.set_model(model_id, provider=None)` | — | Fire-and-forget model switch, safe from sync handlers |
| `tau.reload()` | — | Schedule an extension reload and return immediately |
| `await tau.exec(cmd, args=None, cwd=None)` | `ExecResult` | Run a shell command |

Valid thinking levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`,
`ultra`.

**Properties**

| Property | Type | Description |
|---|---|---|
| `tau.config` | `dict` | Per-extension settings from `settings.json` |
| `tau.settings` | `SettingsManager` | Full settings access |
| `tau.cwd` | `Path` | Working directory at session startup |
| `tau.model_id` | `str` | Active model identifier |
| `tau.provider_id` | `str` | Active provider identifier |

### ExtensionContext

Passed to every event, command, and shortcut handler. It is a live snapshot bound to a
runtime *generation* — see [Hot Reload](#hot-reload) for staleness rules.

**Session information**

| Property | Type | Description |
|---|---|---|
| `ctx.cwd` | `Path` | Working directory |
| `ctx.model_id` | `str` | Active model, e.g. `"claude-sonnet-4-6"` |
| `ctx.provider_id` | `str` | Active provider, e.g. `"anthropic"` |
| `ctx.model_thinking` | `bool` | Whether the active model supports extended thinking |
| `ctx.llm` | `TextLLM \| None` | Live LLM for your own model calls; `None` outside a session |
| `ctx.settings` | `SettingsManager \| None` | Settings access |
| `ctx.mode` | `str` | `"tui"` or `"headless"` |
| `ctx.has_ui` | `bool` | True when dialog-capable UI is available (TUI **or** RPC) |
| `ctx.ui` | `UIContext \| RpcUIContext \| None` | UI API; check `.supports_components` before rendering one |
| `ctx.branch_entries` | `list[SessionEntry]` | Current branch only |
| `ctx.session_entries` | `list[SessionEntry]` | Every branch |

**Agent state**

| Property | Type | Description |
|---|---|---|
| `ctx.phase` | `AgentPhase` | `idle`, `turn`, `compaction`, or `branch_summary` |
| `ctx.streaming_message` | `AssistantMessage \| None` | The partial response being streamed |
| `ctx.pending_tool_call_ids` | `frozenset[str]` | Tool calls started but not finished |
| `ctx.error_message` | `str \| None` | Most recent engine error |
| `ctx.queued_messages` | `dict[str, list[LLMMessage]]` | Keys `steering` and `followup` |
| `ctx.signal` | `asyncio.Event \| None` | Abort signal while streaming; set when aborted. `None` when idle |

| Method | Returns | Description |
|---|---|---|
| `ctx.is_idle()` | `bool` | True when `phase` is `IDLE` |
| `ctx.abort()` | — | Cancel the current turn; no-op when idle |
| `ctx.shutdown()` | — | Exit Tau |
| `ctx.get_context_usage()` | `dict \| None` | Keys `tokens`, `context_window`, `percent` |
| `ctx.compact(custom_instructions=None)` | — | Trigger compaction, fire-and-forget |
| `ctx.get_system_prompt()` | `str` | The effective system prompt |
| `ctx.get_system_prompt_options()` | `dict` | Keys `skills`, `prompts`, `tools`, `system_prompt_length` |
| `ctx.has_pending_messages()` | `bool` | True when steering or follow-ups are queued |

```python
@tau.on("turn_start")
async def guard_context(event, ctx):
    usage = ctx.get_context_usage()
    if usage and usage["percent"] and usage["percent"] > 90:
        ctx.abort()
        if ctx.ui:
            ctx.ui.notify("Context nearly full — aborting turn", "warning")
```

**Session control** — all `async`, all available from event and command handlers.

| Method | Returns | Description |
|---|---|---|
| `await ctx.wait_for_idle()` | — | Suspend until the invocation and its post-run processing finish |
| `await ctx.new_session(options=None)` | `{"cancelled": bool}` | Start a fresh session |
| `await ctx.fork(entry_id, options=None)` | `{"cancelled": bool}` | Fork from a session entry |
| `await ctx.navigate_tree(target_id, *, summarize=False, custom_instructions=None, options=None)` | `{"cancelled": bool}` | Jump to another branch |
| `await ctx.switch_session(session_path, options=None)` | `{"cancelled": bool}` | Switch session files |
| `await ctx.set_model(model_id, provider=None)` | `bool` | Switch the active model; only safe while idle |
| `await ctx.send_message(content)` | — | Append a plain user turn |
| `await ctx.send_user_message(content, deliver_as="steer", *, trigger_turn=False)` | — | Inject into the steering or follow-up queue |
| `await ctx.reload()` | — | Request an extension/resource reload |
| `await ctx.is_project_trusted()` | `bool \| None` | Trust state; `None` when undecided |
| `ctx.set_project_trusted(trusted, *, remember=False)` | — | Set trust; `remember` persists to `~/.tau/trust.json` |
| `await ctx.select(title, options)` | `str \| None` | Option picker; `None` headless or on cancel |
| `await ctx.confirm(title, message="")` | `bool` | Yes/No dialog |

Option dataclasses: `NewSessionOptions(parent_session, with_session)`,
`ForkOptions(position, with_session)`,
`NavigateTreeOptions(summarize, custom_instructions, replace_instructions, label)`, and
`SwitchSessionOptions(with_session)`. A `with_session(ctx)` callback runs inside the new
session before the UI transitions — use it to seed the session with
`ctx.send_user_message`.

`send_user_message` with `deliver_as="steer"` inserts mid-turn; `"follow_up"` queues for
after the current turn. With `trigger_turn=True` and an idle agent, the message starts a
new turn immediately and is rendered as a normal user message.

### UIContext

See [TUI: Widgets, Dialogs, and Overlays](#tui-widgets-dialogs-and-overlays) for the
narrative version. Summary of the full surface:

| Group | Methods |
|---|---|
| Dialogs | `select`, `confirm`, `prompt`, `editor`, `custom`, `custom_inline` |
| Overlays | `show_overlay`, `has_active_selector` |
| Widgets | `set_widget`, `remove_widget` |
| Footer | `set_footer`, `restore_footer`, `set_status`, `clear_status` |
| Messages | `notify`, `clear_messages` |
| Chrome | `set_header`, `set_title`, `set_working_message`, `set_working_visible`, `set_working_indicator`, `set_hidden_thinking_label` |
| Editor | `get_editor_text`, `set_editor_text`, `paste_to_editor`, `set_editor_component`, `get_editor_component`, `get_input_text`, `set_input_text`, `clear_input`, `insert_input_text`, `backspace_input`, `set_input_placeholder`, `reset_input_placeholder`, `set_input_cursor`, `reset_input_cursor` |
| Theme | `theme`, `get_all_themes`, `set_theme` |
| Tool display | `get_tools_expanded`, `set_tools_expanded`, `get_tool_results_expanded`, `set_tool_results_expanded` |
| Input | `on_terminal_input`, `request_render` |

## End-to-End Example

A complete extension that exercises most capabilities: a manifest with a settings
schema, a tool, a slash command, lifecycle hooks with resource cleanup, a status widget,
a published service, and durable session state.

```text
.tau/extensions/
└── notes/
    ├── manifest.json
    ├── __init__.py
    └── store.py
```

```json
{
  "tau": {
    "name": "Notes",
    "author": "you",
    "settings": {
      "title": "Notes",
      "fields": [
        {
          "key": "enabled",
          "label": "Enabled",
          "type": "bool",
          "default": true,
          "description": "Register the note tool and /notes command."
        },
        {
          "key": "max_notes",
          "label": "Max notes",
          "type": "int",
          "default": 50,
          "description": "Notes retained per branch."
        }
      ]
    }
  }
}
```

```python
# notes/store.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Note:
    id: int
    text: str


class NoteStore:
    """Branch-scoped note list, rebuilt by replaying session entries."""

    def __init__(self, limit: int = 50) -> None:
        self._limit = limit
        self.notes: list[Note] = []

    def add(self, text: str) -> Note:
        note = Note(id=len(self.notes) + 1, text=text)
        self.notes.append(note)
        del self.notes[: max(0, len(self.notes) - self._limit)]
        return note

    def rebuild(self, entries) -> None:
        from tau.session.types import CustomInfoEntry

        self.notes.clear()
        for entry in entries:
            if isinstance(entry, CustomInfoEntry) and entry.custom_type == "notes:add":
                self.add(entry.data["text"])

    def lines(self) -> list[str]:
        return [f"{n.id}. {n.text}" for n in self.notes]
```

```python
# notes/__init__.py
from __future__ import annotations

from pydantic import BaseModel, Field

from tau.tool.types import Tool, ToolInvocation, ToolKind, ToolResult

from .store import NoteStore

WIDGET_KEY = "notes"


class NoteSchema(BaseModel):
    text: str = Field(..., description="The note to record")


class NoteTool(Tool):
    def __init__(self, store: NoteStore, on_add) -> None:
        super().__init__(
            name="note",
            description="Record a short note for later reference in this session.",
            schema=NoteSchema,
            kind=ToolKind.Write,
        )
        self._store = store
        self._on_add = on_add

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context=None,
    ) -> ToolResult:
        note = self._store.add(invocation.params["text"])
        self._on_add(note)
        return ToolResult.ok(
            invocation.id,
            f"Recorded note {note.id}.",
            metadata={"note_id": note.id, "total": len(self._store.notes)},
        )


def register(tau) -> None:
    if not tau.config.get("enabled", True):
        return

    store = NoteStore(limit=int(tau.config.get("max_notes", 50)))

    # Publish for other extensions.
    tau.provide("notes", store)

    def _persist(note) -> None:
        tau.append_entry("notes:add", {"text": note.text})

    def _sync(ctx) -> None:
        if ctx.ui is None:
            return
        if store.notes:
            ctx.ui.set_widget(WIDGET_KEY, store.lines()[-3:], placement="above_editor")
        else:
            ctx.ui.remove_widget(WIDGET_KEY)

    tau.register_tool(NoteTool(store, _persist))

    # Rebuild from the current branch so notes follow forks and tree navigation.
    def _rebuild(event, ctx) -> None:
        store.rebuild(ctx.branch_entries)
        _sync(ctx)

    tau.on("session_start", _rebuild)
    tau.on("session_tree", _rebuild)
    tau.on("tui_ready", lambda event, ctx: _sync(ctx))

    @tau.on("extension_unload")
    async def _cleanup(event, ctx) -> None:
        if ctx.ui is not None:
            ctx.ui.remove_widget(WIDGET_KEY)

    @tau.on("runtime_stop")
    async def _stop(event, ctx) -> None:
        store.notes.clear()

    async def cmd_notes(ctx, args) -> None:
        if ctx.ui is None:
            return
        if args and args[0] == "clear":
            if await ctx.ui.confirm("Clear notes?", f"Deletes {len(store.notes)} note(s)."):
                store.notes.clear()
                _sync(ctx)
                ctx.ui.notify("Notes cleared.")
            return
        ctx.ui.notify("\n".join(store.lines()) if store.notes else "No notes yet.")

    tau.register_command(
        "notes",
        "Show recorded notes, or 'clear' to remove them",
        cmd_notes,
        argument_hint="[clear]",
        requires_idle=False,
    )

    tau.append_prompt("Use the `note` tool to record decisions worth remembering.")
```

Drop it in, run `/reload`, and `/notes` lists what the model recorded. Toggling
`Enabled` in `/settings` persists the change and reloads just this extension.

## Hot Reload

`/reload` re-discovers and reloads extensions, skills, prompts, themes, and settings
without restarting the session or starting a new one.

Reload is triggered by:

| Trigger | Scope |
|---|---|
| `/reload` | All extensions |
| `tau.reload()` / `await ctx.reload()` | All extensions |
| Toggling an extension in `/extensions` | All extensions, batched on panel close |
| Granting project trust | All extensions |
| Changing a value in a manifest-generated `/settings` sub-panel | That one extension, falling back to a full reload |

There is no filesystem watcher — reload is always explicitly triggered.

**Serialization.** Reload requests are serialized and coalesced. A request made from
inside an extension callback, or while the agent is running, is deferred until both the
callback pipeline and the agent lifecycle settle. `await ctx.reload()` therefore
acknowledges the request but does not guarantee that a deferred reload has completed.

**Order of operations on a full reload:**

1. Settings are re-read from disk (skipped while `/settings` is open in batch mode).
2. Resources are re-discovered and re-applied.
3. The runtime's extension generation is bumped.
4. `extension_unload` is emitted to every currently loaded extension.
5. The old extension runtime unsubscribes from the hooks bus; the service registry is cleared.
6. Extensions are re-discovered and every `register(tau)` runs again with fresh config.
7. Commands, tools, and the system prompt are swapped in place.
8. `extension_reloaded` is emitted to every newly loaded extension.

**Single-extension reload** re-runs only the target's `register`, swapping its tools,
commands, and prompt contributions. Other extensions are not re-run, so their resources
and side effects are untouched. Only the target's own published services are evicted. It
falls back to a full reload when the target cannot be resolved or is an inline factory.
On load failure the old extension is kept and the error is surfaced.

**Context staleness.** An `ExtensionContext` captures the runtime generation when it is
built, and every property and method asserts that it is still current. A context
retained across `/reload`, `/new`, `/resume`, `/clone`, a fork, or shutdown raises
`StaleExtensionContextError` on use. Do not hold contexts in long-lived background
tasks — capture immutable values, or acquire a fresh context from a new callback.

| Category | Behaviour across reload |
|---|---|
| Picked up | New or modified extension files; new or modified skills and prompts; `settings.json` changes (global and project); new themes |
| Persists | The current session and its branching state; auth credentials; third-party modules already cached in `sys.modules`; anything the extension did not explicitly release |
| Resets | All tools, commands, shortcuts, event handlers, message renderers, autocomplete providers, and published services; the system prompt; extension entry-module globals and inline factory state |

Because normally imported third-party modules stay cached in `sys.modules`, edits to a
helper module that your entry file imports are *not* guaranteed to take effect on
reload — only the entry file is re-executed with a fresh synthetic module name. Restart
Tau when iterating on deep helper modules.

Extensions holding external resources must release them from `extension_unload` (a
reload is coming) and `runtime_stop` (the process is exiting). `session_shutdown` only
fires on session transitions and quit, so it is not sufficient on its own — the
`sandbox` example handles all three for exactly this reason.

After reload, new tools are available to the model on the very next turn.

## Debugging

**Load errors** are collected as `ExtensionError` records with the extension path, the
event (`load` for registration failures), the error message, and a full traceback. They
are reported in the `/reload` output and surfaced in the TUI. A load failure never
crashes startup and never prevents other extensions from loading.

**Handler errors** are caught per handler, logged, and appended to the same error list.
The next handler for the event still runs, and the failing handler contributes no result
to interception.

**Common failure modes:**

| Symptom | Cause |
|---|---|
| `No 'register(tau)' function in <file>` | The module has no module-level `register` callable |
| `ModuleNotFoundError` for a sibling module | Import it relatively (`from .store import NoteStore`) — a directory extension is a package |
| `ImportError: attempted relative import with no known parent package` | The module was imported by bare name (often in a test); load the extension package and reach submodules through it |
| A sibling import returns another extension's module | Bare-name sibling imports share one global namespace — use relative imports |
| Extension silently absent | Filename or directory starts with `_`, or it is disabled in `extensions.list` / `/extensions` |
| Only one copy of a duplicated extension loads | Same-identity priority: project beats global beats builtin |
| A handler's return value does nothing | The event is not in the interceptable allowlist — see [Dispatch model](#dispatch-model) |
| `StaleExtensionContextError` | A context was retained across a reload or session replacement |
| `ctx.ui` is `None` early in startup | Subscribe to `tui_ready` instead of `session_start` (TUI only — in RPC mode `ctx.ui` is ready before `session_start`) |
| `custom_inline()` returns `None` and the caller crashes | You are in RPC mode — gate on `ctx.ui.supports_components` |
| Native import fails after a dependency install | The project `.venv` targets a different Python than the interpreter running Tau |

**Dependency install state** can be inspected with `tau doctor`, which uses the same
venv resolution and dependency digest as the loader. The install cache lives in
`<venv>/.tau_ext_deps.json`; delete the relevant entry to force a reinstall.

**Runtime logs** go to `~/.tau/logs/<session>.log`. A frozen TUI with an apparently
working agent almost always means a render exception was swallowed — check that log
first.

## Next Steps

- [Creating Tools](creating-tools.md) — a deeper walkthrough of the tool contract
- [Extension Settings](extension-settings.md) — settings schemas and the `/settings` panel
- [Tools](tools.md) — the built-in tool set
- [Skills](skills.md) — extending what the model knows rather than what it can do
- [Themes](themes.md) — the full theme schema
- [Inference Providers](inference-providers.md) — provider and model architecture
- [Python API](python-api.md) — embedding Tau and using inline extension factories
- [Keybindings](keybindings.md) — the action map that shortcuts compete with
