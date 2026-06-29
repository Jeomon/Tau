# Settings

This page documents tau's configuration system and all available settings.

## Configuration Locations

Settings files are JSON, with project settings overriding global settings:

| Location | Scope |
|----------|-------|
| `~/.tau/settings.json` | Global — defaults for all projects |
| `.tau/settings.json` | Project — overrides for this project |

Settings are merged at startup (project wins). Edit files directly; changes take effect on the next session start or after `/reload`.

---

## `/settings` TUI Panel

Run `/settings` to open a full interactive settings panel — no need to edit JSON manually for most settings.

### Navigation

| Key | Action |
|-----|--------|
| ↑ / ↓ | Move between items |
| Enter | Toggle a boolean, cycle an enum, or open a sub-panel |
| Escape | Go back to the parent panel (or close if at the top level) |

### Sub-panels

The following settings groups open as **nested sub-panels** (press Enter on the group heading, navigate, press Escape to return):

- **Proxy** — `url`, `no_proxy`, `headers` (JSON)
- **Retry** — `enabled`, `max_retries`, `base_delay_ms`
- **Compaction** — `enabled`, `reserve_tokens`, `keep_recent_tokens`
- **Branch Summary** — `enabled`, `skip_prompt`, `reserve_tokens`
- **Terminal** — `shell_path`, `shell_command_prefix`
- **Extensions** — each extension that registers settings items (see [Extension settings panel](#extension-settings-panel))

### Text input mode

Integer settings (timeouts, token counts, padding values, etc.) use **inline text editing**:

1. Navigate to the item and press Enter to enter edit mode.
2. Type the new value.
3. Press Enter to confirm, or Escape to cancel without saving.

### Settings exposed in the panel

All settings documented in the reference below are editable from the TUI. Settings that were previously JSON-only and are now accessible in the panel include:

| Setting | Panel location |
|---------|----------------|
| `project_trust` | Top level — cycles `"ask"` / `"always"` / `"never"` |
| `double_escape_action` | Top level — cycles `"fork"` / `"tree"` / `"none"` |
| `tree_filter_mode` | Top level — cycles all five modes |
| `show_hardware_cursor` | Top level — boolean toggle |
| `http_idle_timeout_ms` | Top level — text input |
| `picker_max_visible` | Top level — text input |
| `autocomplete_max_visible` | Top level — text input |
| `editor_padding_x` | Top level — text input |
| All retry sub-settings | Retry sub-panel |
| All compaction sub-settings | Compaction sub-panel |
| All branch summary sub-settings | Branch Summary sub-panel |
| All proxy sub-settings | Proxy sub-panel |

### Extension settings panel

Extensions can register their own settings items that appear at the bottom of the `/settings` list as named sub-panels. See [Extension Settings](extension-settings.md#exposing-settings-in-the-settings-panel) for details.

---

## Settings Reference

All field names use `snake_case`.

### Model & Provider

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Default provider, e.g. `"anthropic"`, `"openai"` |
| `model` | string | Default model ID, e.g. `"claude-sonnet-4-6"` |
| `thinking_level` | string | Extended thinking budget level (see below) |
| `transport` | string | Transport layer override (`"streaming"` or `"polling"`) |
| `enabled_models` | list[string] | Restrict the model picker to these model IDs |

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "thinking_level": "low"
}
```

#### Extended thinking

Extended thinking gives the model a private scratchpad — a reasoning trace it works through before producing its response. It trades token budget (and cost) for accuracy on complex tasks like multi-step reasoning, planning, and math.

| Level | Token budget | When to use |
|-------|-------------|-------------|
| `none` / `off` | 0 | Everyday tasks, fast responses |
| `minimal` | ~1 024 | Light reasoning, quick sanity checks |
| `low` | ~4 096 | Moderate reasoning |
| `medium` | ~8 192 | Multi-step problems |
| `high` | ~16 384 | Complex analysis |
| `xhigh` | ~32 768 | Hard reasoning tasks |
| `max` | ~65 536 | Most demanding tasks |

Token budgets are approximate defaults. Override per-level with `thinking_budgets` (see below). Not all providers support extended thinking — it is silently ignored when unsupported.

Toggle during a session with `/effort` or the effort picker.

### UI & Display

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `theme` | string | `"default"` | Theme name: `"default"`, `"dracula"`, `"nord"`, `"gruvbox"`, `"catppuccin"`, or a custom name |
| `show_thinking` | boolean | `true` | Show extended-thinking blocks in the message list |
| `show_tool_calls` | boolean | `true` | Show tool call / result blocks in the message list |
| `picker_max_visible` | integer | `8` | Max visible rows in the model / theme picker |
| `autocomplete_max_visible` | integer | `5` | Max visible rows in the editor autocomplete dropdown |
| `tool_result_preview_lines` | integer | `5` | Lines shown before default-shell tool results collapse; values below 1 are clamped |
| `show_hardware_cursor` | boolean | `false` | Keep the terminal cursor visible while it is repositioned (aids IME input) |
| `editor_padding_x` | integer | `0` | Horizontal padding (spaces) added inside the input editor |
| `double_escape_action` | string | `"fork"` | What happens when Escape is pressed twice on an empty editor: `"fork"` clones the current branch, `"tree"` opens the branch navigator, `"none"` does nothing |
| `tree_filter_mode` | string | `"default"` | Default message filter when `/tree` opens: `"default"`, `"no-tools"`, `"user-only"`, `"labeled-only"`, `"all"` |

#### Hardware Cursor & Editor Padding

**`show_hardware_cursor`** — Enable this if you're using IME (input method editor) for CJK, Arabic, or other languages. When enabled, the terminal cursor remains visible as it's repositioned for text input, improving the IME experience. Default is `false` for standard keyboard input.

**`editor_padding_x`** — Add horizontal spacing inside the text input area. Useful if you prefer visual breathing room around your typing. Each unit equals one space character. Default is `0` (no padding).

Example with both enabled:
```json
{
  "theme": "dracula",
  "show_thinking": false,
  "show_tool_calls": true,
  "show_hardware_cursor": true,
  "editor_padding_x": 2
}
```

### Message Delivery

Tau maintains two internal queues for messages that arrive while the agent is already running:

- **Steering queue** — high-priority messages that redirect the current turn (e.g. "stop, do this instead")
- **Follow-up queue** — messages that queue behind the current turn (e.g. "after that, also do X")

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `steering_mode` | string | `"one_at_a_time"` / `"all"` | How many queued steering messages to drain per turn |
| `follow_up_mode` | string | `"one_at_a_time"` / `"all"` | How many queued follow-up messages to drain per turn |

`"one_at_a_time"` (default) — processes one queued message per agent turn. The agent completes its response, then picks up the next queued message. This gives the model a chance to acknowledge each instruction before moving on.

`"all"` — drains the entire queue at once, concatenating all pending messages into a single user turn. Use this when you want to batch several follow-up instructions without the model stopping between them.

```json
{
  "steering_mode": "one_at_a_time",
  "follow_up_mode": "all"
}
```

### Session

| Field | Type | Description |
|-------|------|-------------|
| `session_dir` | string | Custom session storage directory (default `~/.tau/sessions`) |

### Context Compaction

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compaction.enabled` | boolean | `true` | Enable automatic context compaction |
| `compaction.reserve_tokens` | integer | `16384` | Tokens reserved for LLM response before compaction triggers |
| `compaction.keep_recent_tokens` | integer | `20000` | Recent message tokens to preserve verbatim (not summarized) |

#### How Compaction Works

When your conversation grows long, tau automatically summarizes older messages to free up context space. The settings control this behavior:

- **`enabled`**: Turn compaction on/off entirely. When `false`, the `/compact` command prints `"Compaction is disabled. Enable it in /settings → Compaction."` instead of running.
- **`reserve_tokens`**: How many tokens to keep available for the LLM's response (default 16,384 ≈ 4K-5K words)
- **`keep_recent_tokens`**: How many recent message tokens to preserve word-for-word before summarization kicks in (default 20,000 ≈ 5K-6K words)

The active values are resolved live, so changes made through `/settings` apply
without restarting Tau. For models with smaller input limits, Tau clamps both
budgets and reserves additional space for the generated summary. Automatic and
overflow-triggered compaction are both disabled when `enabled` is `false`.

#### Mid-turn (threshold) compaction

In addition to compaction triggered at the start of a turn, tau can compact **mid-turn** when context usage reaches `input_limit - reserve_tokens` while the agent is already running. When this happens the engine stops cleanly so you can review the compacted context and continue — no partial output is lost. Tool output lines are truncated before sending to the LLM to help prevent hitting the threshold in the first place.

Example configurations:

```json
{
  "compaction": {
    "enabled": true,
    "reserve_tokens": 16384,
    "keep_recent_tokens": 20000
  }
}
```

More aggressive compaction (smaller context window):
```json
{
  "compaction": {
    "reserve_tokens": 8192,
    "keep_recent_tokens": 10000
  }
}
```

Disable compaction (for unlimited context models):
```json
{
  "compaction": {
    "enabled": false
  }
}
```

See [Sessions](sessions.md#context-compaction) for a full explanation of the compaction algorithm.

### Branch Summary

When you use `/tree` to navigate to a different branch, tau asks whether to generate a
summary of the branch you're leaving.  These settings control that behaviour.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `branch_summary.enabled` | boolean | `true` | Enable branch summarisation entirely. When `false`, the summarisation selector is never shown when switching branches in `/tree` |
| `branch_summary.skip_prompt` | boolean | `false` | Skip the "Summarize branch?" picker and navigate without a summary |
| `branch_summary.reserve_tokens` | integer | `16384` | Output/headroom budget reserved when generating the branch summary; clamped to the active model's input limit |

Disable branch summarization entirely:
```json
{
  "branch_summary": {
    "enabled": false
  }
}
```

Enable summarization but skip the confirmation prompt:
```json
{
  "branch_summary": {
    "enabled": true,
    "skip_prompt": true
  }
}
```

### Images

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image.auto_resize` | boolean | `true` | Resize images to 2000×2000 max before sending to the LLM |
| `image.block_images` | boolean | `false` | Prevent all images from being sent to the LLM |

```json
{
  "image": {
    "auto_resize": true,
    "block_images": false
  }
}
```

### Retry

| Field | Type | Description |
|-------|------|-------------|
| `retry.enabled` | boolean | Enable automatic retry on transient errors |
| `retry.max_retries` | integer | Maximum retry attempts |
| `retry.base_delay_ms` | integer | Base delay between retries in milliseconds |
| `retry.provider.timeout_ms` | integer | Per-provider request timeout override |
| `retry.provider.max_retries` | integer | Per-provider retry limit |
| `retry.provider.max_retry_delay_ms` | integer | Per-provider max retry delay |

These settings are applied to the LLM client at startup:

- Setting `retry.enabled = false` sets `max_retries = 0` on the LLM client, disabling all automatic retries.
- `retry.max_retries` and `retry.base_delay_ms` are passed directly to the client when enabled.

Changes take effect on the next session start or after `/reload`.

#### Adaptive retry delay

When a provider responds with a `Retry-After` or `Retry-After-Ms` header (common on rate-limit responses), tau uses that value as the retry delay instead of the configured `base_delay_ms`. The delay is capped at **60 seconds** so a provider that returns an extreme value (e.g. "retry in 16 days") doesn't stall the agent indefinitely. When no header is present, `base_delay_ms` is used.

```json
{
  "retry": {
    "enabled": true,
    "max_retries": 5,
    "base_delay_ms": 500
  }
}
```

### Thinking Budgets

Override token budgets for each thinking level:

```json
{
  "thinking_budgets": {
    "minimal": 1024,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
    "max": 65536
  }
}
```

### Network

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `http_idle_timeout_ms` | integer | `60000` | Idle timeout for LLM HTTP streams in milliseconds |
| `websocket_connect_timeout_ms` | integer | — | WebSocket connect/open handshake timeout in milliseconds |
| `http_proxy.url` | string | — | Proxy URL for both HTTP and HTTPS (overrides env vars) |
| `http_proxy.no_proxy` | string | — | Comma-separated hosts to exclude from proxying |
| `http_proxy.headers` | object | — | Custom headers for proxy authentication |

```json
{
  "http_idle_timeout_ms": 120000,
  "http_proxy": {
    "url": "http://proxy.example.com:8080",
    "no_proxy": "localhost,127.0.0.1,internal.corp",
    "headers": {
      "Proxy-Authorization": "Basic dXNlcjpwYXNz"
    }
  }
}
```

### Terminal

| Field | Type | Description |
|-------|------|-------------|
| `terminal.shell_path` | string | Shell binary for command execution (default: system shell) |
| `terminal.shell_command_prefix` | string | Lines prepended inside the shell before each command |

`terminal.shell_path` switches the shell binary. `terminal.shell_command_prefix` is prepended as script lines before every command — useful for sourcing environments or enabling shell options:

```json
{
  "terminal": {
    "shell_path": "/bin/zsh",
    "shell_command_prefix": "source ~/.zshrc"
  }
}
```

### Startup

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `quiet_startup` | boolean | `false` | Suppress the startup notice |

### Project Trust

When tau detects project-local configuration (a `.tau/` directory, `.agents/skills/`, or `AGENTS.md`/`CLAUDE.md`), it asks before loading it. This prevents a checked-out repository from silently overriding your global tools, prompts, settings, or injecting unwanted instructions.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project_trust` | string | `"ask"` | Global trust policy: `"ask"` prompts on first use, `"always"` trusts all projects automatically, `"never"` always blocks project config |

**Trust policy options:**
- `"ask"` (default) — Prompt the first time project files are discovered
- `"always"` — Automatically load all project files (extensions, settings, context files)
- `"never"` — Never load project files without explicit `--approve` flag

Trust decisions are stored in `~/.tau/trust.json` — once you approve a directory, tau remembers it. Trusting a parent directory implicitly trusts all subdirectories.

**Override for a single run:**
```bash
tau --approve              # Trust project files for this run
tau --no-approve           # Don't trust project files for this run
tau --no-context-files     # Disable only context file (AGENTS.md/CLAUDE.md) loading
```

See [Project Context Files](project-context.md) for details on AGENTS.md/CLAUDE.md configuration.

### Extensions

| Field | Type | Description |
|-------|------|-------------|
| `extensions.enabled` | boolean | Global on/off switch for all extensions |
| `extensions.list` | list | Per-extension entries (see [Extensions](extensions.md)) |

Each entry in `extensions.list`:

```json
{
  "extensions": {
    "enabled": true,
    "list": [
      {
        "path": "~/.tau/extensions/my_ext.py",
        "enabled": true,
        "settings": { "api_key": "sk-..." }
      }
    ]
  }
}
```

---

## Implementation Status

### Hardware Cursor & Editor Padding

Both `show_hardware_cursor` and `editor_padding_x` settings are **fully implemented and working**:

- **`show_hardware_cursor`**: When enabled in settings, the terminal cursor is displayed during IME text input, improving the experience for users typing in CJK, Arabic, or other languages requiring input method editors. The cursor is repositioned as you type and shows at the current input position.

- **`editor_padding_x`**: Adds horizontal spacing (in spaces) around the text input area. When set to `2`, for example, you get 2 spaces of padding on the left and right of the editor. This is a visual preference setting for those who like more breathing room while typing.

### Compaction Settings

All compaction settings are **fully implemented and configurable**:

- **`compaction.enabled`**: Toggle automatic context compaction on/off
- **`compaction.reserve_tokens`**: Control how many tokens to keep available for LLM responses
- **`compaction.keep_recent_tokens`**: Control how many recent message tokens to preserve before summarization

Settings take effect immediately when you change them in `~/.tau/settings.json` and reload the session or restart Tau.

### Retry Settings

All retry settings are **fully implemented and wired to the LLM client at startup**:

- **`retry.enabled`**: Toggle automatic retry on transient errors (default: `false`). Setting this to `false` sets `max_retries=0` on the LLM client.
- **`retry.max_retries`**: Maximum number of retry attempts (default: `3`). Applied to the LLM client when `retry.enabled` is `true`.
- **`retry.base_delay_ms`**: Base delay between retries in milliseconds (default: `1000`). Applied to the LLM client when `retry.enabled` is `true`.
- **`retry.provider`**: Per-provider overrides for `timeout_ms`, `max_retries`, and `max_retry_delay_ms`

### Thinking Budgets

All thinking budget settings are **fully implemented and configurable**:

- **`thinking_budgets.minimal`**: Token budget for "minimal" level (default: `1024`)
- **`thinking_budgets.low`**: Token budget for "low" level (default: `4096`)
- **`thinking_budgets.medium`**: Token budget for "medium" level (default: `8192`)
- **`thinking_budgets.high`**: Token budget for "high" level (default: `16384`)
- **`thinking_budgets.xhigh`**: Token budget for "xhigh" level (default: `32768`)
- **`thinking_budgets.max`**: Token budget for "max" level (default: `65536`)

Override these to customize extended thinking token allocations for your workflow.

---

## Configuration Inheritance

Settings are resolved in this order (later wins):

1. Built-in defaults
2. Global `~/.tau/settings.json`
3. Project `.tau/settings.json`

---

## Resetting to Defaults

Delete the relevant settings file:

```bash
rm .tau/settings.json        # remove project overrides
rm ~/.tau/settings.json      # remove global settings
```

Or remove individual keys from the JSON.

---

## Next Steps

- [Sessions](sessions.md) — Session management and compaction
- [Themes](themes.md) — Customize appearance
- [Extensions](extensions.md) — Extension configuration
- [Extension Settings](extension-settings.md) — Typed configuration for extensions with nested structure support
- [HTTP Proxy](http-proxy.md) — HTTP proxy configuration via environment variables
