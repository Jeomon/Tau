# Extensions

Extensions are plain Python files that hook into tau's lifecycle. Each one exports
a single `register(tau)` function — everything is wired up there.

Extension tools and commands may shadow built-ins while loaded. Tau retains the
shadowed source layer, so disabling or reloading the extension restores the
previous implementation. Reload also replaces extension message renderers,
autocomplete providers, shortcuts, and published services; do not retain
service objects across reload.

## File locations

tau loads extensions from three sources, in this order:

| Priority | Location | Scope |
|----------|----------|-------|
| Highest | `.tau/extensions/` in the project root | Project-only |
| | `~/.tau/extensions/` | Global (all projects) |
| Lowest | Explicit paths in `extensions.list` | Named entries |

Files starting with `_` are skipped. Disabled stems (via `extensions.list[].enabled: false`)
are skipped. Duplicate resolved paths are deduplicated silently.

### Subdirectory packages

A subdirectory under `extensions/` is loaded if it has either:
- `manifest.json` with `{"tau": {"extensions": ["./main.py"]}}` — loads the declared files
- `__init__.py` — loaded as a package entry point

`manifest.json` can also declare third-party dependencies:

```json
{"tau": {"dependencies": ["ddgs>=9.0"]}}
```

Before the entry file is executed, these specs are installed into the project or
global packages venv (`uv pip install`, falling back to `pip` when `uv` isn't on
PATH) and the venv's site-packages directory is appended to `sys.path`. Tau's
runtime environment keeps precedence, so an extension cannot replace Tau's own
dependencies with incompatible versions. The install only runs once — a hash of
the dependency list is cached, so unchanged manifests are a no-op on subsequent
launches.

## Installable resource packages

`tau install` accepts PyPI names, pip-compatible Git URLs, local distributions,
and direct wheel or source-archive URLs:

```bash
tau install pypi:tau-tools==1.2.3
tau install git+ssh://git@github.com/example/tau-tools.git@v1
tau install ./dist/tau_tools-1.2.3-py3-none-any.whl
tau install https://packages.example.com/tau_tools-1.2.3-py3-none-any.whl
tau install tau-tools==1.2.3 --index-url https://packages.example.com/simple
```

Repeat `--extra-index-url URL` to resolve dependencies from additional indexes.
Index configuration is retained for future updates.

Packages can bundle extensions, skills, prompts, and themes:

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

Conventional resource directories are discovered when a manifest field is
omitted. Package entries in `settings.json` support an `enabled` switch and
optional `extensions`, `skills`, `prompts`, and `themes` path filters. An empty
filter disables that resource type. Use `tau update --all` to update Tau and
all installed packages.

## Entry point

Every extension must export a `register(tau)` function:

```python
# .tau/extensions/my_ext.py

def register(tau):
    # wire up tools, commands, event handlers …
    pass
```

### Inline extension factories

Python API users can supply the same registration function directly through
`RuntimeConfig`, without creating or installing an extension file:

```python
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

Inline factories receive the normal `ExtensionAPI`, can be synchronous or
asynchronous, and load after file-based extensions. They participate in the
same tool, command, hook, provider, service, prompt, theme, and UI registration
systems. Factory failures are isolated and reported as `ExtensionError`.

`/reload` unloads the old inline extensions and executes the factories again,
preventing duplicate hook subscriptions. Session replacement reuses the active
extension runtime and does not rerun factories.

`register` may be `async` if setup requires awaiting:

```python
async def register(tau):
    data = await tau.exec("git", ["log", "--oneline", "-5"])
    tau.append_prompt(f"Recent commits:\n{data.stdout}")
```

---

## Tools

Import from `tau.tool.types`. Use a Pydantic `BaseModel` for the parameter schema.

```python
from pydantic import BaseModel, Field
from tau.tool.types import (
    Tool, ToolKind, ToolExecutionMode,
    ToolInvocation, ToolResult, ToolContext,
)

class _Schema(BaseModel):
    path: str = Field(..., description="File path to analyse")
    verbose: bool = Field(default=False, description="Include line details")

class CountLocTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="count_loc",
            description="Count lines of code in a file.",
            schema=_Schema,
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
        path = invocation.params.get("path")
        try:
            lines = len(open(path).readlines())
        except OSError as e:
            return ToolResult.error(invocation.id, str(e))

        metadata = {"path": path, "line_count": lines}
        return ToolResult.ok(invocation.id, f"{path}: {lines} lines", metadata=metadata)

def register(tau):
    tau.register_tool(CountLocTool())
```

### `ToolKind`

| Value | Meaning |
|-------|---------|
| `ToolKind.Read` | Reads files or external data |
| `ToolKind.Edit` | Modifies existing files |
| `ToolKind.Write` | Creates or overwrites files |
| `ToolKind.Execute` | Runs shell commands or processes |
| `ToolKind.Web` | Network requests |

### `ToolExecutionMode`

| Value | Meaning |
|-------|---------|
| `ToolExecutionMode.Sequential` | Run one at a time (default) |
| `ToolExecutionMode.Parallel` | Run concurrently with other parallel tools |
| `ToolExecutionMode.Batch` | Group with other batch tools, then run together |

### `ToolResult`

```python
ToolResult.ok(invocation.id, "content", metadata={"key": "value"})
ToolResult.error(invocation.id, "error message")
```

`metadata` is a `dict[str, Any]`. Follow the pattern of builtin tools: record the
inputs that shaped the operation plus output stats (counts, sizes, flags) — not
content that's already in the text result.

### `ToolContext`

`context` is injected by the engine and provides:

```python
context.llm    # live TextLLM instance — call LLM APIs from inside a tool
context.cwd    # Path — working directory
```

---

## Commands

Register slash commands with `tau.register_command`. The handler receives
`(ctx: ExtensionContext, args: list[str])`.

```python
async def cmd_hello(ctx, args):
    name = args[0] if args else "world"
    print(f"Hello, {name}! cwd={ctx.cwd}")

def register(tau):
    tau.register_command("hello", "Say hello", cmd_hello)
    # Now accessible as /hello [name]
```

`aliases` registers additional trigger words:

```python
tau.register_command("hello", "Say hello", cmd_hello, aliases=["hi", "hey"])
```

### Argument completions

Register a callback to provide dynamic tab-completions for your command's arguments. When the user types `/mycommand <text>`, the dropdown is populated by calling `get_argument_completions` with the current text after the command name:

```python
from tau.tui.autocomplete import AutocompleteItem

def get_branch_completions(prefix: str) -> list[AutocompleteItem]:
    branches = ["main", "dev", "feat/login", "fix/typo"]
    return [
        AutocompleteItem(label=b, description="git branch")
        for b in branches if b.startswith(prefix)
    ]

def register(tau):
    tau.register_command(
        "checkout",
        "Switch git branch",
        cmd_checkout,
        get_argument_completions=get_branch_completions,
    )
```

`get_argument_completions` may be a regular or `async` function. It receives the text typed after the command name (may be empty) and returns a list of `AutocompleteItem`.

---

### `ExtensionContext` reference

`ctx` is passed to every event handler, command handler, and shortcut handler. It provides a live snapshot of session state at the moment the handler fires.

#### Session information

| Property | Type | Description |
|----------|------|-------------|
| `ctx.cwd` | `Path` | Working directory |
| `ctx.model_id` | `str` | Active model, e.g. `"claude-sonnet-4-6"` |
| `ctx.provider_id` | `str` | Active provider, e.g. `"anthropic"` |
| `ctx.llm` | `TextLLM \| None` | Active text LLM for running your own model calls (`await ctx.llm.invoke(LLMContext(...))`); `None` outside a live session |
| `ctx.settings` | `SettingsManager \| None` | Full settings access |
| `ctx.mode` | `str` | `"tui"` when running in the interactive terminal, `"headless"` otherwise |
| `ctx.has_ui` | `bool` | True when dialog-capable UI is available — guards TUI-only calls |
| `ctx.ui` | `UIContext \| None` | TUI customisation API (see [UIContext](#uicontext--tui-customisation)) |
| `ctx.branch_entries` | `list[SessionEntry]` | Entries on the current branch only (root → current leaf) |
| `ctx.session_entries` | `list[SessionEntry]` | All entries across every branch in the session file |

#### Agent state

| Property | Type | Description |
|----------|------|-------------|
| `ctx.phase` | `AgentPhase` | Current lifecycle phase: `idle`, `turn`, `compaction`, or `branch_summary` |
| `ctx.streaming_message` | `AssistantMessage \| None` | Current partial assistant response |
| `ctx.pending_tool_call_ids` | `frozenset[str]` | Snapshot of tool calls that have started but not finished |
| `ctx.error_message` | `str \| None` | Most recent engine error |
| `ctx.queued_messages` | `dict[str, list[LLMMessage]]` | Snapshots of the steering and follow-up queues |

| Method | Returns | Description |
|--------|---------|-------------|
| `ctx.is_idle()` | `bool` | `True` when `ctx.phase` is `AgentPhase.IDLE` |
| `ctx.abort()` | — | Cancel the current agent turn; no-op if already idle |
| `ctx.shutdown()` | — | Exit tau gracefully |
| `ctx.get_context_usage()` | `dict \| None` | Token usage info (see below) |
| `ctx.compact(custom_instructions=None)` | — | Trigger context compaction (fire-and-forget; safe to call mid-turn) |
| `ctx.get_system_prompt()` | `str` | The current effective system prompt |
| `ctx.has_pending_messages()` | `bool` | `True` if steering or follow-up messages are queued (useful to avoid injecting a duplicate) |
| `ctx.signal` | `asyncio.Event \| None` | The current abort signal while the agent is streaming; the event is set when the turn is aborted. `None` when idle |
| `ctx.get_system_prompt_options()` | `dict` | Metadata about how the active system prompt was assembled — keys: `skills`, `prompts`, `tools`, `system_prompt_length` |

Extensions can inject a user message with
`await ctx.send_user_message(content, deliver_as="steer", trigger_turn=False)`.
Set `trigger_turn=True` to start a new turn immediately when the agent is idle;
while it is busy, the message is queued using the selected delivery mode. In
interactive mode, an immediately triggered message is rendered as a normal user
message before the assistant response.

`get_context_usage()` returns a dict with three keys, or `None` if token data is not yet available (e.g. immediately after compaction, before the next LLM response):

```python
usage = ctx.get_context_usage()
if usage:
    tokens  = usage["tokens"]           # int | None
    window  = usage["context_window"]   # int
    percent = usage["percent"]          # float | None
```

Example — abort if context is almost full:

```python
@tau.on("turn_start")
async def guard_context(event, ctx):
    usage = ctx.get_context_usage()
    if usage and usage["percent"] and usage["percent"] > 90:
        ctx.abort()
        ctx.ui.notify("Context nearly full — aborting turn", "warning")
```

#### Session control

These methods are `async` and are available in both event handlers and command handlers.

| Method | Returns | Description |
|--------|---------|-------------|
| `await ctx.wait_for_idle()` | — | Suspend until the active invocation and its post-run save/compaction processing finish |
| `await ctx.new_session()` | `{"cancelled": bool}` | Start a fresh session |
| `await ctx.fork(entry_id)` | `{"cancelled": bool}` | Fork from a specific session entry |
| `await ctx.navigate_tree(target_id, summarize=False, custom_instructions=None)` | `{"cancelled": bool}` | Jump to another branch in the session tree |
| `await ctx.switch_session(path)` | `{"cancelled": bool}` | Switch to a different session file |
| `await ctx.reload()` | — | Reload extensions, skills, prompts, and themes |

#### Project trust

Extensions can inspect and override trust decisions for the current project directory.

| Method | Returns | Description |
|--------|---------|-------------|
| `await ctx.is_project_trusted()` | `bool \| None` | Current trust state — checks the `project_trust` hook first, then the settings manager. `None` means undecided |
| `ctx.set_project_trusted(trusted, *, remember=False)` | — | Set trust for this session. `remember=True` persists the decision to `~/.tau/trust.json` |

The `project_trust` hook event lets an extension intercept the trust prompt entirely:

```python
from tau.hooks.types import ProjectTrustResult

def register(tau):
    @tau.on("project_trust")
    async def auto_trust(event, ctx):
        # Trust this specific project automatically
        if "/my-safe-org/" in event.project_dir:
            return ProjectTrustResult(trusted=True, remember=True)
        return None  # fall through to the default prompt
```

All session-control methods return a dict with a `cancelled` key — `True` if an extension handler blocked the operation:

```python
@tau.register_command("clean-start", "Fork then compact")
async def cmd(ctx, args):
    sm = ctx._session_manager
    if sm is None:
        return
    leaf = sm.get_leaf_id()
    result = await ctx.fork(leaf)
    if result["cancelled"]:
        ctx.ui.notify("Fork was blocked by another extension.")
        return
    ctx.compact(custom_instructions="Keep only the final conclusions.")
```

`navigate_tree` optionally generates a branch summary and attaches it to the
destination branch. If summary generation fails, navigation still completes
without a summary:

```python
result = await ctx.navigate_tree(target_id, summarize=True)
```

Extensions can replace the default branch-summary model call:

```python
from tau.hooks import SessionBeforeTreeResult

def register(tau):
    @tau.on("session_before_tree")
    async def summarize(event, ctx):
        entries = event.preparation.entries_to_summarize
        summary = await my_summarizer(entries)
        return SessionBeforeTreeResult(
            summary=summary,
            summary_details={"source": "my-extension"},
        )
```

#### `branch_entries` vs `session_entries`

The session file stores entries from every branch ever taken — forks, navigations, abandoned paths. `session_entries` returns all of them. `branch_entries` returns only the linear path from the root to the current leaf — the entries actually in scope for the current conversation.

**Always use `branch_entries` when restoring per-branch extension state**, so you don't accidentally read data from an abandoned branch:

```python
from tau.session.types import CustomInfoEntry

@tau.on("session_start")
async def restore(event, ctx):
    # Walk newest → oldest to find the last saved state on this branch
    for entry in reversed(ctx.branch_entries):
        if isinstance(entry, CustomInfoEntry) and entry.custom_type == "my-ext":
            apply_state(entry.data)
            break
```

Use `session_entries` only when you genuinely need the full cross-branch history (e.g. building a timeline view or counting all tool calls ever made in the file).

---

## Event hooks

Subscribe to lifecycle events with `tau.on`. Handlers always receive
`(event, ctx)` — `event` is the typed event dataclass, `ctx` is an
`ExtensionContext` snapshot.

```python
def register(tau):
    @tau.on("session_start")
    async def on_start(event, ctx):
        print(f"Session started — reason: {event.reason}")

    # Direct call form (no decorator)
    tau.on("agent_end", lambda event, ctx: print(event.reason))
```

### Event reference

**Runtime lifecycle**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `runtime_start` | — | — | Runtime construction begins; extensions not yet loaded. Earliest bootstrap signal |
| `runtime_ready` | — | — | Runtime fully constructed with all tools, extensions, and sessions loaded. Safe to kick off background work |
| `runtime_stop` | — | — | Runtime shutting down after mode-specific loop exits. Last chance for cleanup |

**Session lifecycle**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `session_start` | `reason`, `previous_session_file` | — | Session fully loaded and ready. `reason`: `startup \| new \| resume \| fork \| reload` |
| `session_shutdown` | `reason`, `target_session_file` | — | Session about to close — last chance for cleanup |
| `session_before_switch` | `reason`, `target_session_file` | `SessionBeforeSwitchResult` | About to replace the active session — return `cancel=True` to block |
| `session_before_fork` | `entry_id`, `position` | `SessionBeforeForkResult` | About to create a branch — return `cancel=True` to block |
| `session_before_tree` | `preparation` | `SessionBeforeTreeResult` | About to rewrite the tree. Return `summary=` and optional `summary_details=` to replace default summarization, or `cancel=True` to block |
| `branch_summary_start` | `old_leaf_id`, `target_id`, `from_extension` | — | Branch summarization begins |
| `branch_summary_end` | `old_leaf_id`, `target_id`, `summary_entry_id`, `summary_length`, `from_extension` | — | Summary attached to the destination |
| `branch_summary_failure` | `old_leaf_id`, `target_id`, `error` | — | Summary failed; navigation continues without it |
| `branch_summary_cancelled` | `old_leaf_id`, `target_id`, `reason` | — | Summary or navigation was cancelled |
| `session_tree` | `new_leaf_id`, `old_leaf_id`, `from_extension` | — | Session tree has been rewritten with the new active leaf |

**Agent & turn lifecycle**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `before_agent_start` | `prompt`, `system_prompt` | `BeforeAgentStartEventResult` | User prompt known, engine not yet started. Return `system_prompt` to override it for this turn |
| `agent_start` | — | — | Engine loop starts |
| `agent_end` | `reason`, `messages` | — | Engine loop finished. `reason`: `completed \| aborted \| error` |
| `agent_error` | `error` | — | Engine terminated with an unrecoverable error |
| `turn_start` | `turn_index`, `timestamp` | — | New LLM inference turn begins |
| `turn_end` | `turn_index`, `message`, `tool_results` | — | Turn completes with assistant message and tool results |
| `settled` | — | — | The invocation completed queued turns and post-run compaction with no messages currently queued; this is an observation, not a lock against concurrent submissions |

**Input**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `input` | `text`, `source` | `InputEventResult` | New user message received. `source`: `interactive \| rpc \| extension \| queue`. Return `action="transform"` with `text=` to replace, or `action="handled"` to suppress |
| `context` | `messages` | `ContextEventResult` | Full message history about to be sent to LLM. Return `messages=` to rewrite it |

For dynamic browser or computer-use state that must be refreshed before every
LLM request without being persisted, return `ephemeral_messages` from a
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
                UserMessage.with_images(
                    "Current browser state",
                    images=[screenshot],
                )
            ]
        )
```

The hook runs before every inference, including after tool execution.
`ephemeral_messages` must contain only `UserMessage` objects. They are appended
only to the provider request, are not persisted, and are excluded from
Anthropic prompt-cache breakpoints. Use `messages=` in the same result to
replace the stable request context.

**Streaming & messages**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `message_start` | `message` | — | Model starts streaming |
| `message_update` | `message` | — | Incremental streaming chunk |
| `message_end` | `message` | `MessageEndEventResult` | Response fully received. Return `message=` to swap the stored message |

**Tool calls**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `tool_call` | `tool_call_id`, `tool_name`, `input` | `ToolCallEventResult` | Before tool execution. Return `block=True` to abort, or `params=` to rewrite arguments |
| `tool_result` | `tool_call_id`, `tool_name`, `input`, `content`, `is_error` | `ToolResultEventResult` | After tool execution. Return `content=` to override output, or `terminate=True` to end the loop |
| `tool_execution_start` | `tool_call` | — | Tool handler's `execute()` begins |
| `tool_execution_update` | `partial_tool_result` | — | Streaming progress update from a long-running tool |
| `tool_execution_end` | `tool_result` | — | Tool handler's `execute()` returns |
| `tool_execution_failure` | `tool_name`, `tool_call_id`, `input`, `error` | — | Tool handler raised an uncaught exception |

**Provider**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `before_provider_request` | `model`, `messages`, `options` | — | About to send request to the LLM API |
| `after_provider_response` | `model`, `response` | — | LLM streaming response fully collected |

**Shell commands**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `user_terminal` | `command`, `private`, `cwd` | — | Shell command run on behalf of the user (e.g. via the terminal tool) |
| `terminal_execution` | `message`, `streaming` | — | Fired twice: at start (`streaming=True`) and on completion (`streaming=False`) |
| `terminal_output` | `message` | — | Each output chunk while a `!` command streams |

**Queue**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `queue_update` | `queue`, `message`, `messages` | — | A follow-up or steering message entered the queue. `queue`: `"steering" \| "followup"` |

**Compaction**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `before_compaction` | `preparation`, `entries`, `manual`, `reason`, `will_retry` | `BeforeCompactionResult` | Before summarization; may cancel or provide a complete compaction |
| `compaction_start` | `manual`, `reason`, `will_retry` | — | Compaction begins after interception |
| `compaction_end` | `manual`, `reason`, `will_retry`, `tokens_before`, `summary_length`, `from_extension` | — | Compaction succeeds |
| `compaction_failure` | `manual`, `reason`, `will_retry`, `error` | — | Compaction fails |
| `compaction_cancelled` | `manual`, `reason`, `will_retry` | — | An extension cancels compaction |

**Model & settings**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `model_select` | `model`, `previous_model`, `source` | — | Active model changed. `source`: `set \| cycle \| restore` |
| `thinking_level_select` | `level`, `previous_level` | — | Extended thinking budget level changed |

**Persistence**

| Event | Payload fields | Result type | Description |
|-------|---------------|-------------|-------------|
| `save_point` | — | — | Session writes flushed — state is consistent on disk |

---

## Custom Compaction

Extensions can replace tau's default LLM summarisation by handling `before_compaction`.
Return a `BeforeCompactionResult` to intercept:

| Return field | Type | Effect |
|---|---|---|
| `cancel=True` | `bool` | Abort compaction entirely (raises on manual `/compact`) |
| `compaction=<result>` | `CompactionResult` | Use this summary instead of calling the LLM |
| _(return `None`)_ | — | Fall through to the default algorithm |

Multiple extensions run in registration order. The first non-`None` result wins.
If a handler raises, it is logged and the next handler runs; if all fall through,
the default algorithm runs.

### Example — use a different model for summarisation

```python
from tau.hooks import BeforeCompactionResult
from tau.session.compaction import CompactionResult

def register(tau):
    @tau.on("before_compaction")
    async def handle(event, ctx):
        preparation = event.preparation
        lines = []
        for msg in preparation.messages_to_summarize:
            role = getattr(msg, "role", "unknown")
            text = getattr(msg, "text", "") or ""
            if text:
                lines.append(f"{role}: {text[:200]}")

        summary = await my_summariser("\n".join(lines))
        if not summary:
            return None  # fall through to default

        return BeforeCompactionResult(
            compaction=CompactionResult(
                summary=summary,
                first_kept_entry_id=preparation.first_kept_entry_id,
                tokens_before=preparation.tokens_before,
            )
        )
```

### Example — block automatic compaction

```python
from tau.hooks import BeforeCompactionResult

def register(tau):
    @tau.on("before_compaction")
    def block_auto(event, ctx):
        if not event.manual:
            return BeforeCompactionResult(cancel=True)
```

### `preparation` fields

`event.preparation` is a `CompactionPreparation`:

| Field | Description |
|-------|-------------|
| `messages_to_summarize` | Messages replaced by the summary |
| `turn_prefix_messages` | Messages from a split turn that also need summarising |
| `first_kept_entry_id` | ID of the first session entry kept verbatim after the summary |
| `tokens_before` | Estimated token count before compaction |
| `is_split_turn` | Whether compaction cuts inside an in-progress turn |
| `previous_summary` | Summary text from a prior compaction cycle, if any |

`event.entries` is the full raw session entry list for the current branch.

---

## Keyboard shortcuts

`register_shortcut` takes a literal key combination. Tau compares extension
shortcuts with the effective `KeyMap`.
Safety-critical editor and application bindings are reserved and cannot be
replaced by extensions. An extension may replace a non-reserved global
application binding, with a warning. If multiple extensions register the same
key, the last loaded extension wins and Tau reports a warning.

Some low-level editor operations that are not exposed as `KeyMap` actions,
such as cursor movement, are still consumed directly by the focused editor.
Registering an extension shortcut does not replace those operations. See
[Customising keybindings](keybindings.md#customising-keybindings) for the
available actions.

```python
def register(tau):
    @tau.register_shortcut("ctrl+g", "Open greeter")
    async def on_ctrl_g(ctx):
        print(f"ctrl+g pressed — cwd={ctx.cwd}")
```

Direct call form:

```python
tau.register_shortcut("ctrl+g", "Open greeter", my_handler)
```

---

## System prompt appends

```python
def register(tau):
    tau.append_prompt("Always respond in the active project's language.")
```

The text is appended verbatim to the end of the system prompt at session start
and after `/reload`.

---

## Themes

```python
from tau.tui.theme import LayoutTheme, SpinnerTheme

def register(tau):
    tau.register_theme("ocean", LayoutTheme(
        primary="#0ea5e9",
        secondary="#38bdf8",
        spinner=SpinnerTheme(label_thinking="thinking…"),
    ))
```

After registration the theme appears in the `/theme` picker.

---

## Per-extension configuration

Read settings from `tau.config` — populated from the `settings` dict of the
matching entry in `extensions.list`:

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
    api_key = tau.config.get("api_key", "")
    verbose  = tau.config.get("verbose", False)
```

`tau.config` is an empty dict when no matching entry exists or no `settings` key
is present.

---

## Flags (env-backed config)

For values that shouldn't live in `settings.json` (tokens, secrets):

```python
def register(tau):
    tau.register_flag("token", type="str", env="MY_EXT_TOKEN", default="")
    token = tau.get_flag("token")  # reads MY_EXT_TOKEN or returns ""
```

`type` is `"str"` | `"bool"` | `"int"`. The env var is read at call time so
changes in the process environment are picked up without restart.

---

## Shell execution

Run shell commands from inside event or command handlers:

```python
def register(tau):
    @tau.on("session_start")
    async def on_start(event, ctx):
        result = await tau.exec("git", ["log", "--oneline", "-5"])
        if result.code == 0:
            tau.append_prompt(f"Recent commits:\n{result.stdout}")
```

`tau.exec` returns `ExecResult(stdout, stderr, code)`.  `cwd` defaults to the
session working directory; pass an explicit `cwd=` to override.

---

## Editor autocomplete providers

Extensions can inject custom autocomplete suggestions into the editor. When the user
types a registered trigger character, a dropdown appears and filters as they type.

```python
from tau.tui.autocomplete import AutocompleteItem

def register(tau):
    async def issue_items(ctx):
        # ctx.trigger == "#", ctx.query == text typed after "#"
        results = await search_issues(ctx.query)
        return [
            AutocompleteItem(
                label=f"#{issue.id}",
                description=issue.title,
                # insert_text defaults to label when omitted
            )
            for issue in results
        ]

    tau.add_autocomplete_provider("#", issue_items, description="GitHub issues")
```

**`AutocompleteItem` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `label` | `str` | Displayed in the picker (required) |
| `description` | `str` | Dimmed secondary text shown to the right |
| `insert_text` | `str \| None` | Text inserted into the editor; defaults to `label` |

**`AutocompleteContext` fields available inside `get_items`:**

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | Full editor text at call time |
| `cursor_pos` | `int` | Character index of the cursor |
| `trigger` | `str` | The trigger character (e.g. `"#"`) |
| `query` | `str` | Text typed after the trigger up to the cursor |

**Keyboard behaviour** once the dropdown is open:

| Key | Action |
|-----|--------|
| ↑ / ↓ or Ctrl+P / Ctrl+N | Navigate items |
| Tab or Enter | Accept selection |
| Escape | Dismiss without selecting |
| Any other key | Updates the filter query |

The provider's `get_items` may be a regular function or an `async` function — both are
supported. Sync providers populate the picker immediately; async providers run in the
background and the picker appears as soon as results arrive.

**Sync example:**

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

---

## Registration-time API summary

### Registration (`tau.*`)

| Method | Description |
|--------|-------------|
| `tau.register_tool(tool)` | Add a tool the agent can call |
| `tau.register_command(name, desc, handler, aliases=[], get_argument_completions=None, argument_hint=None, requires_idle=True)` | Add a `/name` slash command; set `requires_idle=False` only for UI-only or read-only handlers safe during an active turn |
| `tau.on(event, handler)` / `@tau.on(event)` | Subscribe to a lifecycle event |
| `tau.register_shortcut(key, desc, handler)` | Bind a literal keyboard shortcut |
| `tau.append_prompt(text)` | Append text to the system prompt |
| `tau.register_theme(name, theme)` | Add a named theme to the picker |
| `tau.register_message_renderer(type, fn)` | Render custom message types in the TUI |
| `tau.add_autocomplete_provider(trigger, fn, desc)` | Register an editor autocomplete provider |
| `tau.register_settings(items, title, on_change)` | Expose a named sub-panel in the `/settings` TUI panel (see [Extension Settings](extension-settings.md#exposing-settings-in-the-settings-panel)) |
| `tau.register_provider(id, config)` | Register a custom LLM provider (see [Custom Providers](#custom-providers)) |
| `tau.unregister_provider(id)` | Remove all models registered under a provider |
| `tau.register_flag(name, type, env, default)` | Declare an env-backed flag |
| `tau.get_flag(name)` | Read a registered flag's value |
| `await tau.exec(cmd, args, cwd)` | Run a shell command |
| `tau.config` | Per-extension settings dict from `settings.json` |
| `tau.cwd` | `Path` — working directory at session start |
| `tau.model_id` | Active model identifier |
| `tau.provider_id` | Active provider identifier |

### Live session (`tau.*` from event/command handlers)

These methods call through the runtime reference, so they are safe to call from any handler that fires after session startup. They are **not** available during `register(tau)` itself (the session does not exist yet at that point).

#### Session naming and labels

```python
def register(tau):
    @tau.on("session_start")
    async def name_session(event, ctx):
        import datetime
        tau.set_session_name(datetime.date.today().isoformat())

    @tau.on("agent_end")
    async def bookmark_end(event, ctx):
        sm = ctx._session_manager
        if sm:
            leaf = sm.get_leaf_id()
            tau.set_label(leaf, "agent-end")
```

| Method | Returns | Description |
|--------|---------|-------------|
| `tau.set_session_name(name)` | — | Set the display name shown in the session picker |
| `tau.get_session_name()` | `str \| None` | Current session display name |
| `tau.set_label(entry_id, label)` | — | Set a label on a session entry; pass `None` to clear |

#### Tool management

Inspect or replace the agent's active tool set at runtime. Useful for plan-mode extensions that swap tools depending on what phase the agent is in.

```python
@tau.register_command("tools-off", "Disable all tools for this turn")
async def cmd_no_tools(ctx, args):
    tau.set_active_tools([])   # agent still runs, but has no tools
    ctx.ui.notify("Tools disabled.")

@tau.register_command("tools-on", "Re-enable all tools")
async def cmd_yes_tools(ctx, args):
    tau.set_active_tools([])   # empty list = restore all
    ctx.ui.notify("Tools re-enabled.")
```

| Method | Returns | Description |
|--------|---------|-------------|
| `tau.get_active_tools()` | `list[str]` | Names of tools currently visible to the agent |
| `tau.get_all_tools()` | `list[dict]` | All registered tools: `[{"name": …, "description": …}]` |
| `tau.set_active_tools(names)` | — | Restrict agent to named tools; empty list restores all |

#### Commands

```python
# List all slash commands registered in the current session
cmds = tau.get_commands()
# [{"name": "compact", "description": "…"}, {"name": "tree", "description": "…"}, …]
```

| Method | Returns | Description |
|--------|---------|-------------|
| `tau.get_commands()` | `list[dict]` | All registered commands: `[{"name": …, "description": …}]` |

#### Thinking level

Read or change the extended-thinking budget at runtime. Valid level strings mirror the settings values: `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"`.

```python
@tau.register_command("think-hard", "Switch to high thinking for the next turn")
async def cmd_think(ctx, args):
    previous = tau.get_thinking_level()
    tau.set_thinking_level("high")
    ctx.ui.notify(f"Thinking level: {previous} → high")
```

| Method | Returns | Description |
|--------|---------|-------------|
| `tau.get_thinking_level()` | `str` | Current level, e.g. `"low"` |
| `tau.set_thinking_level(level)` | — | Change the level; silently ignored if the string is invalid |

---

## Extension Patterns

### Pattern 1 — Single file

The simplest pattern. One `.py` file, one `register` function.

```text
.tau/extensions/
    web.py
```

```python
# web.py
def register(tau):
    tau.register_tool(MyTool())
    tau.register_command("fetch", "Fetch a URL", cmd_fetch)
```

Use this when the extension is small and self-contained.

---

### Pattern 2 — Multiple flat files

Split a large extension into focused files, each with its own `register`. Tau loads them all in alphabetical order.

```text
.tau/extensions/
    web_tools.py        # tools only
    web_commands.py     # commands only
    web_hooks.py        # event handlers only
```

Each file is independent. There is no shared module state between them unless you use a shared import.

---

### Pattern 3 — Directory with `__init__.py`

A directory whose `__init__.py` is the entry point. No manifest needed — the loader picks it up automatically.

```text
.tau/extensions/
└── web/
    ├── __init__.py     # register(tau) lives here
    ├── tools.py
    ├── commands.py
    └── hooks.py
```

Because the loader uses `importlib.util.spec_from_file_location`, it does not set up a real package context. Add the directory to `sys.path` at the top of `__init__.py` so sub-imports work:

```python
# web/__init__.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from tools import SearchTool, FetchTool
from commands import cmd_fetch
from hooks import on_start

def register(tau):
    tau.register_tool(SearchTool())
    tau.register_tool(FetchTool())
    tau.register_command("fetch", "Fetch a URL", cmd_fetch)
    tau.on("session_start", on_start)
```

```python
# web/tools.py
from tau.tool.types import Tool, ToolKind, ToolInvocation, ToolResult

class SearchTool(Tool):
    ...
```

A `manifest.json` can sit alongside `__init__.py` purely to declare dependencies,
without an `"extensions"` key — the loader still falls back to `__init__.py` as
the entry point:

```json
// web/manifest.json
{"tau": {"dependencies": ["ddgs>=9.0"]}}
```

---

### Pattern 4 — Directory with `manifest.json`

Explicitly declare one or more entry files via `manifest.json`. Useful when you want the entry point to be named something other than `__init__.py`, or when you have multiple entry files.

```text
.tau/extensions/
└── web/
    ├── manifest.json
    ├── main.py         # entry point declared in manifest
    ├── tools/
    │   ├── search.py
    │   └── fetch.py
    └── utils.py
```

```json
// web/manifest.json
{"tau": {"extensions": ["./main.py"]}}
```

Same `sys.path` rule applies — insert the directory in `main.py`:

```python
# web/main.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from tools.search import SearchTool
from tools.fetch import FetchTool

def register(tau):
    tau.register_tool(SearchTool())
    tau.register_tool(FetchTool())
```

```python
# web/tools/search.py
from tau.tool.types import Tool, ToolKind, ToolInvocation, ToolResult

class SearchTool(Tool):
    ...
```

The `tools/` subdirectory needs its own `__init__.py` to be importable as a package:

```text
web/
└── tools/
    ├── __init__.py     # can be empty
    ├── search.py
    └── fetch.py
```

---

### Pattern 5 — Manifest with multiple entry files

A manifest can declare multiple entry files, each with its own `register`. Useful when different concerns should remain in separate modules but share a common directory.

```text
.tau/extensions/
└── web/
    ├── manifest.json
    ├── tools.py
    └── commands.py
```

```json
// web/manifest.json
{"tau": {"extensions: ["./tools.py", "./commands.py"]}}
```

Each declared file must export `register(tau)`. They are loaded in declaration order.

---

### Choosing a pattern

| Pattern | Best for |
|---------|----------|
| Single file | Simple tools or hooks with no sub-imports |
| Multiple flat files | Splitting a large extension by concern, each file standalone |
| `__init__.py` package | One logical extension with internal modules, cleanest Python idiom |
| `manifest.json` + `main.py` | When you want an explicit named entry point or multiple entry files |
| Manifest + multiple entries | Multiple independent modules that share a directory |

---

### Global vs project scope

```text
~/.tau/extensions/        # global — loaded for every project
.tau/extensions/          # project-local — loaded only in this directory
```

Put general-purpose tools (web fetch, search, code helpers) in `~/.tau/extensions/`. Put project-specific extensions (custom tools for this repo, project hooks) in `.tau/extensions/`. Project extensions load before global ones, so they can override or extend anything registered globally.

---

## UIContext — TUI Customisation

When an extension handler runs inside a TUI session, `ctx.ui` provides live access
to the layout. It is `None` in headless or RPC mode — always guard with `if ctx.ui`.

```python
def register(tau):
    @tau.on("session_start")
    async def on_start(event, ctx):
        if ctx.ui is None:
            return
        ctx.ui.set_status("my-ext", "● connected")
```

### `ctx.ui` methods

**Widgets** — inject components above or below the editor:

```python
ctx.ui.set_widget("banner", ["  Warning: uncommitted changes  "], placement="above_editor")
ctx.ui.remove_widget("banner")
```

`placement` is `"above_editor"` (default) or `"below_editor"`. The `id` is used to
update or remove the widget later. Pass a list of strings for static text, or a
`Component` instance for interactive widgets.

**Footer** — replace the built-in footer bar with a static component or a factory function:

```python
ctx.ui.set_footer(my_component)       # static component
ctx.ui.set_footer(my_factory)         # factory function (called on every render)
ctx.ui.restore_footer()               # revert to default
```

Factory signatures (tau detects arity automatically):

```python
# No context — simple replacement
def my_footer():
    return StaticComponent(["  Custom footer  "])

# With TUI + theme
def my_footer(tui, theme):
    return StaticComponent([f"  {theme.primary}Custom footer{RESET}  "])

# With TUI + theme + live data (recommended)
from tau.tui.ui_context import FooterData

def my_footer(tui, theme, data: FooterData):
    branch = data.git_branch or "detached"
    pct    = f"{data.context_percent:.0f}%" if data.context_percent is not None else "—"
    model  = data.model_id
    return StaticComponent([f"  {branch}  {model}  ctx:{pct}  "])
```

`FooterData` fields:

| Field | Type | Description |
|-------|------|-------------|
| `git_branch` | `str` | Current git branch name, or `""` |
| `context_tokens` | `int \| None` | Estimated tokens used so far |
| `context_window` | `int` | Model's total context window size |
| `context_percent` | `float \| None` | `context_tokens / context_window * 100` |
| `active_extensions` | `list[str]` | Names of loaded extensions |
| `model_id` | `str` | Active model identifier |
| `provider_id` | `str` | Active provider identifier |

**Status slots** — add named text to the footer's middle section:

```python
ctx.ui.set_status("git", "main ↑2")   # add or update
ctx.ui.clear_status("git")            # remove
```

Multiple extensions can each own their own slot — they are keyed by `id` and do
not interfere with each other.

**Editor** — replace or restore the input editor component:

```python
ctx.ui.set_editor_component(lambda theme, keybindings: MyEditor(theme, keybindings))
ctx.ui.set_editor_component(None)       # restore the default editor
factory = ctx.ui.get_editor_component() # read back the installed factory (None = default)
```

**Render** — request a repaint after changing widget content outside of a handler:

```python
ctx.ui.request_render()
```

### Interactive overlays (async)

Command handlers can be `async` and `await` these overlay methods to collect input
from the user via the same pickers and prompts that built-in commands use:

**`ctx.ui.select`** — show a scrollable picker:

```python
tau.register_command("pick-theme", "Choose a colour", pick_theme)

async def pick_theme(ctx, args):
    if not ctx.ui:
        return
    choice = await ctx.ui.select("Choose a colour", ["Red", "Green", "Blue"])
    if choice:
        ctx.ui.notify(f"You picked: {choice}")
```

**`ctx.ui.confirm`** — show a Yes / No prompt:

```python
async def dangerous_cmd(ctx, args):
    if not ctx.ui:
        return
    ok = await ctx.ui.confirm("Delete everything?", "This cannot be undone.")
    if ok:
        do_delete()
```

**`ctx.ui.prompt`** — show a single-line text input:

```python
async def set_key(ctx, args):
    if not ctx.ui:
        return
    key = await ctx.ui.prompt("Enter API key", secret=True)
    if key:
        save_key(key)
```

All three overlay methods return `None` / `False` when the user presses Escape.

For a full multi-line editor dialog, see [Multi-line editor dialog](#multi-line-editor-dialog) below.

**`ctx.ui.notify`** — inline notification (synchronous, no await needed):

```python
ctx.ui.notify("Operation complete")
ctx.ui.notify("Something went wrong", "error")   # type hint accepted; styling coming soon
```

---

### Header / title

```python
# Persistent banner above the message list
from tau.tui.component import StaticComponent
ctx.ui.set_header(StaticComponent(["── My Extension ──"]))
ctx.ui.set_header(None)   # remove

# Terminal window/tab title
ctx.ui.set_title("My Agent – session 42")
```

### Spinner / working indicator

```python
ctx.ui.set_working_message("Fetching data…")   # override spinner label
ctx.ui.set_working_message(None)               # revert to default
ctx.ui.set_working_visible(False)              # hide spinner entirely
ctx.ui.set_working_visible(True)               # re-show
ctx.ui.set_working_indicator(["◐","◓","◑","◒"], interval_ms=80)  # custom animation
ctx.ui.set_working_indicator()                 # revert to theme default
```

### Thinking label

```python
ctx.ui.set_hidden_thinking_label("reasoning…")   # custom collapsed label
ctx.ui.set_hidden_thinking_label()               # reset to "thinking…"
```

### Editor content

```python
text = ctx.ui.get_editor_text()           # read current editor content
ctx.ui.set_editor_text("Write a poem")   # replace editor content
ctx.ui.paste_to_editor("@file.py ")      # insert at cursor position
```

### Theme

```python
names = ctx.ui.get_all_themes()          # list[str] of theme names
ok = ctx.ui.set_theme("dark")            # True on success, False if unknown
theme = ctx.ui.theme                     # LayoutTheme — current active theme
```

### Tool call visibility

```python
expanded = ctx.ui.get_tools_expanded()   # bool
ctx.ui.set_tools_expanded(False)         # collapse all tool-call blocks
ctx.ui.set_tools_expanded(True)          # expand them again
```

### Raw terminal input subscription

```python
def on_key(event):
    if event.matches("ctrl+p"):
        open_my_panel()

unsub = ctx.ui.on_terminal_input(on_key)
# call unsub() to remove the handler
```

### Multi-line editor dialog

`ctx.ui.editor()` opens a floating multi-line text editor overlay and returns when the user is done. `Ctrl+S` (or `Ctrl+Enter`) saves and closes; `Escape` cancels. Arrow keys, `Home`/`End`, `Backspace`, and `Enter` (newline) all work normally.

Returns the edited text as a string, or `None` if the user cancelled.

```python
@tau.register_command("edit-prompt", "Edit the system prompt addition for this session")
async def cmd_edit(ctx, args):
    if ctx.ui is None:
        return
    current = ctx.get_system_prompt()
    text = await ctx.ui.editor("System prompt", prefill=current)
    if text is None:
        return  # user cancelled
    ctx.ui.notify(f"Saved {len(text)} characters")
```

Signature:

```python
text: str | None = await ctx.ui.editor(title: str, prefill: str = "")
```

### Custom editor component

Replace the main input editor with your own component. The factory receives the current input theme and keybindings manager and must return a `Component` that implements the same text-input interface as `TextInput`.

```python
from tau.tui.components.text_input import TextInput

class ModalInput(TextInput):
    """Minimal vi-style input with normal/insert modes."""
    ...

@tau.on("session_start")
async def install_editor(event, ctx):
    if ctx.ui:
        ctx.ui.set_editor_component(
            lambda theme, keybindings: ModalInput(theme, keybindings)
        )

# Restore the built-in editor (pass None):
ctx.ui.set_editor_component(None)

# Read back the currently installed factory (None = default):
factory = ctx.ui.get_editor_component()
```

---

## Custom Providers

Extensions can add or remove LLM providers at registration time. Providers registered this way appear in `/model` alongside built-in providers.

```python
def register(tau):
    tau.register_provider("my-llm", {
        "base_url": "https://api.my-llm.com/v1",
        "api_key": "$MY_LLM_API_KEY",   # reads env var at runtime
        "api": "openai-responses",
        "auth_header": True,             # add Authorization: Bearer <key>
        "models": [
            {"id": "fast-7b",  "name": "My Fast 7B",  "context_length": 8192},
            {"id": "smart-70b","name": "My Smart 70B", "context_length": 32768},
        ],
    })
```

To remove a provider (e.g. if credentials are revoked):

```python
tau.unregister_provider("my-llm")
```

**Provider config fields:**

| Field | Type | Description |
|-------|------|-------------|
| `base_url` | string | API endpoint URL |
| `api_key` | string | Literal value, `$ENV_VAR` (read at runtime), or `!shell-command` |
| `api` | string | API type: `"anthropic-messages"`, `"openai-responses"`, etc. |
| `auth_header` | bool | Automatically add `Authorization: Bearer <key>` header |
| `headers` | dict | Extra HTTP headers |
| `models` | list | Model definitions with `id`, `name`, `context_length`, etc. |
| `oauth` | dict | OAuth config: `login()`, `refresh_token()`, `get_api_key()` |

---

## Registration vs Runtime

There are two distinct API objects with different scopes:

| | `ExtensionAPI` (`tau`) | `ExtensionContext` (`ctx`) |
|-|----------------------|--------------------------|
| **When available** | Inside `register(tau)` only | Inside event/command/shortcut handlers |
| **What it is** | Static wiring — stores registrations to apply later | Live session snapshot at dispatch time |
| **Session state** | Not available | `ctx.cwd`, `ctx.model_id`, `ctx.ui`, … |
| **Register things** | `tau.register_tool()`, `tau.on()`, … | Cannot register new things |

```python
def register(tau):
    # ← you are here: ExtensionAPI scope
    # tau.config is available, but no live session yet

    @tau.on("agent_end")
    async def handler(event, ctx):
        # ← you are here: ExtensionContext scope
        # ctx.cwd, ctx.model_id, ctx.ui are live
        pass
```

---

## Error Handling

Errors in extension handlers are **isolated** — a failing handler does not crash
the session or block other handlers.

- If a handler raises an exception it is caught, logged to the session's error
  buffer, and the next registered handler for that event runs.
- For `before_compaction`, if all handlers raise, the default algorithm runs.
- Errors are surfaced in `/reload` output and in the session info panel.

To inspect errors programmatically:

```python
@tau.on("session_start")
async def on_start(event, ctx):
    # Errors from previous dispatch cycles are not exposed via ctx.
    # Watch the reload output or the TUI error panel.
    pass
```

---

## Reloading

`/reload` re-discovers and hot-reloads all extensions, skills, prompts, and
settings without restarting the session.

Reload requests are serialized. A request made from an extension callback or
while the agent is processing is queued until the callback pipeline and complete
agent lifecycle settle. Multiple queued requests are coalesced. Consequently,
`await ctx.reload()` acknowledges a request but does not guarantee that a
deferred reload has already completed.

Contexts are generation-bound. An `ExtensionContext` captured before `/reload`,
`/new`, `/resume`, `/clone`, or runtime shutdown raises
`StaleExtensionContextError` when reused. Do not retain contexts in long-lived
background tasks; capture immutable values or acquire a fresh context from a
new callback.

**What gets picked up:**
- New or modified extension files
- New or modified skill and prompt template files
- Changes to `settings.json` (both global and project)
- New themes in the themes directories

**What persists across reload:**
- The current session (messages, branching state)
- Auth credentials
- Normally imported third-party Python modules, which remain cached in `sys.modules`
- External state not explicitly released by the extension

**What resets:**
- All registered tools, commands, shortcuts, and event handlers — re-registered from scratch
- The system prompt — rebuilt from SYSTEM.md and all `append_prompt` calls
- Extension entry-module globals and inline factory state

Tau emits `extension_unload` before replacing an extension runtime and
`runtime_stop` before shutdown. Use those events to release subprocesses,
background tasks, sockets, and registrations outside Tau.

After reload, new tools are available to the model immediately in the next turn.
