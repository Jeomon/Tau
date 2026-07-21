# Settings

Tau uses JSON settings files with project settings overriding global settings. Every key is `snake_case` and maps directly onto a field of the `Settings` dataclass in `tau/settings/types.py`.

| Location | Scope |
|----------|-------|
| `~/.tau/settings.json` | Global — defaults for all projects |
| `.tau/settings.json` | Project — overrides for the current directory |

Edit the files directly, or use `/settings` for an interactive panel. Changes take effect on the next session start or after `/reload`.

## Table of Contents

- [Precedence and Merging](#precedence-and-merging)
- [The `/settings` Panel](#the-settings-panel)
- [Model & Thinking](#model--thinking)
- [UI & Display](#ui--display)
- [Message Delivery](#message-delivery)
- [Sessions](#sessions)
- [Compaction](#compaction)
- [Branch Summary](#branch-summary)
- [Retry](#retry)
- [Images](#images)
- [Network](#network)
- [Terminal & Execution](#terminal--execution)
- [Startup & Telemetry](#startup--telemetry)
- [Project Trust](#project-trust)
- [Extensions](#extensions)
- [Packages](#packages)
- [Full Example](#full-example)
- [Recovering a Corrupt Settings File](#recovering-a-corrupt-settings-file)

## Precedence and Merging

Global and project settings are deep-merged at startup. A non-`null` project value wins; nested objects merge field by field rather than replacing wholesale.

```json
// ~/.tau/settings.json (global)
{
  "theme": "dark",
  "compaction": { "enabled": true, "reserve_tokens": 16384 }
}

// .tau/settings.json (project)
{
  "compaction": { "reserve_tokens": 8192 }
}

// Merged result
{
  "theme": "dark",
  "compaction": { "enabled": true, "reserve_tokens": 8192 }
}
```

Three rules govern the merge:

1. Project settings are only merged when the project is **trusted**. An untrusted project contributes nothing — see [Project Trust](#project-trust).
2. `telemetry` is read from the **global scope only**, so a project can never re-enable telemetry a user has turned off.
3. Every setter on `SettingsManager` writes to the **global** file. The only exception is `set_project_extension_list()`, which writes `.tau/settings.json`. `/settings` therefore always edits global settings.

Writes are field-scoped: only the keys you changed are re-serialized and merged back into whatever is on disk, so a concurrent editor's unrelated keys survive.

### LLM Option Precedence

Model options reach a provider through `TextLLM._merge_options()` (`tau/inference/api/text/service.py`), which merges layer by layer:

```text
provider base options  (tau/builtins/providers/*)
  └─ model.base_url override        (per-model, when the model declares one)
      └─ caller-supplied LLMOptions (settings, CLI flags, extensions)
```

An override field only wins when it is **both** non-`None` **and** explicitly set by the caller. `LLMOptions` tracks assignment in `_explicit_fields` (`tau/inference/types.py`), so a field left at its dataclass default — `temperature`, `max_retries`, `timeout` — does not clobber the provider's own configured value. This is why passing a bare `LLMOptions()` is a no-op rather than a reset.

Settings feed this layer at runtime: when `retry.enabled` is true, `max_retries` and `retry_base_delay_ms` are assigned onto the live `llm.api.options` (`tau/runtime/types.py`).

## The `/settings` Panel

Run `/settings` for an interactive panel over most of the reference below.

| Key | Action |
|-----|--------|
| ↑ / ↓ | Move between rows |
| Enter / Space | Cycle a value, open a sub-panel, or enter text-edit mode |
| Tab | Cycle tabs |
| Escape | Cancel text edit, close a sub-panel, or close the panel |
| Backspace | Delete a search character (or an edit character while editing) |
| *(type)* | Fuzzy-search rows |

These groups open as nested sub-panels: **Proxy**, **Retry**, **Compaction**, **Branch summary**, **Terminal**, plus one panel per extension that registers settings. See [Extension Settings](extension-settings.md).

Integer and string settings use inline text editing: press Enter to edit, Enter again to confirm, Escape to discard.

## Model & Thinking

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `model` | object | – | Per-modality model selection; see below |
| `thinking_level` | string | – | `"off"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"`, `"ultra"` |
| `thinking_budgets` | object | – | Per-level token budgets; see below |
| `transport` | string | `"auto"` | Wire transport: `"auto"`, `"http"`, `"websocket"`, `"sse"` |
| `enabled_models` | string[] | – | Restrict the model picker to these model IDs |

### model

`model` is an object of per-modality `{id, provider, voice}` references. `text` is the chat model; `voice` is speech-to-text, `speak` is text-to-speech.

```json
{
  "model": {
    "text":  { "id": "claude-sonnet-4-6", "provider": "anthropic" },
    "voice": { "id": "whisper-1", "provider": "openai" },
    "speak": { "id": "tts-1", "provider": "openai", "voice": "nova" }
  }
}
```

| Slot | Modality | Aliases accepted by the API |
|------|----------|------------------------------|
| `text` | Chat / completion | – |
| `voice` | Speech-to-text (input) | `stt`, `audio` |
| `speak` | Text-to-speech (output) | `tts` |
| `image` | Image generation | – |
| `video` | Video generation | – |

`voice` inside a `ModelRef` is the provider's named TTS voice, not a model slot.

> **Legacy format:** a flat `"model": "<id>"` with a sibling `"provider"` key still loads — it is folded into `model.text` and rewritten in nested form on the next save.

### thinking_budgets

Token budgets per thinking level, used by providers that map a level to `budget_tokens`. Unset levels fall back to the defaults below.

| Level | Default budget |
|-------|----------------|
| `minimal` | `1024` |
| `low` | `2048` |
| `medium` | `4096` |
| `high` | `8192` |
| `xhigh` | `16384` |
| `max` | `32768` |

```json
{
  "thinking_budgets": {
    "medium": 8192,
    "high": 24576
  }
}
```

`off` and `ultra` are valid `thinking_level` values but have no budget entry: `off` disables thinking, `ultra` is provider-defined.

## UI & Display

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `theme` | string | `"dark"` | Theme name, or `"auto"` to pick light/dark from the terminal background. See [Themes](themes.md) |
| `show_thinking` | boolean | `true` | Show extended-thinking blocks in the message list |
| `show_tool_calls` | boolean | `true` | Show tool call and result blocks |
| `show_images` | boolean | `true` | Render image content inline |
| `picker_max_visible` | integer | `8` | Max visible rows in pickers; clamped to a minimum of 1 |
| `external_editor` | string | `$VISUAL`/`$EDITOR` | Command run by `ctrl+g` to compose the prompt (e.g. `"code --wait"`). Falls back to `notepad`/`nano` |
| `autocomplete_max_visible` | integer | `5` | Max visible rows in the editor autocomplete dropdown; minimum 1 |
| `tool_result_preview_lines` | integer | `5` | Lines shown before a shell tool result collapses; minimum 1 |
| `editor_padding_x` | integer | `0` | Horizontal padding inside the input editor; minimum 0 |
| `cursor_blink` | boolean | `true` | Blink the input cursor while idle and focused |
| `show_hardware_cursor` | boolean | `false` | Keep the terminal cursor visible while it is repositioned (helps IME input) |
| `double_escape_action` | string | `"clear"` | Action on double-Escape while idle: `"clear"`, `"fork"`, `"tree"`, `"none"` |
| `tree_filter_mode` | string | `"default"` | Default `/tree` filter: `"default"`, `"no-tools"`, `"user-only"`, `"labeled-only"`, `"all"` |

```json
{
  "theme": "tokyo-night",
  "show_thinking": true,
  "editor_padding_x": 1,
  "double_escape_action": "tree"
}
```

## Message Delivery

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `steering_mode` | string | `"one_at_a_time"` | Queued steering messages drained per turn: `"one_at_a_time"` or `"all"` |
| `follow_up_mode` | string | `"one_at_a_time"` | Queued follow-up messages drained per turn: `"one_at_a_time"` or `"all"` |

Note the underscores: `"one_at_a_time"`, not `"one-at-a-time"`.

## Sessions

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `session_dir` | string | `~/.tau/sessions` | Directory for session storage. Accepts absolute or relative paths, and a leading `~` |

```json
{ "session_dir": "~/work/tau-sessions" }
```

A bare `"~"` resolves to the home directory; `"~/..."` expands against it; anything else is resolved to an absolute path. See [Sessions](sessions.md).

## Compaction

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `compaction.enabled` | boolean | `true` | Enable automatic context compaction |
| `compaction.reserve_tokens` | integer | `16384` | Tokens reserved for the model's response before compaction triggers; minimum 1 |
| `compaction.keep_recent_tokens` | integer | `20000` | Recent message tokens kept verbatim rather than summarized; minimum 1 |

```json
{
  "compaction": {
    "enabled": true,
    "reserve_tokens": 16384,
    "keep_recent_tokens": 20000
  }
}
```

## Branch Summary

Controls the summary offered when navigating between branches in `/tree`.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `branch_summary.enabled` | boolean | `true` | Enable branch summarization. When `false`, the selector is never shown |
| `branch_summary.skip_prompt` | boolean | `false` | Skip the "Summarize branch?" prompt and navigate without a summary |
| `branch_summary.reserve_tokens` | integer | `16384` | Headroom reserved when generating the summary; minimum 1 |

## Retry

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `retry.enabled` | boolean | `false` | Enable automatic retry on transient LLM errors |
| `retry.max_retries` | integer | `3` | Maximum retry attempts; minimum 0 |
| `retry.base_delay_ms` | integer | `1000` | Base delay for exponential backoff, in milliseconds; minimum 1 |

```json
{
  "retry": {
    "enabled": true,
    "max_retries": 5,
    "base_delay_ms": 2000
  }
}
```

Retry is **off by default**. When enabled, the values are applied to the live LLM options at session start.

## Images

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `image.auto_resize` | boolean | `true` | Resize images to 2000×2000 max before sending to the LLM |
| `image.block_images` | boolean | `false` | Prevent all images from being sent to the LLM |

`image.block_images` controls what is *sent*; `show_images` controls what is *rendered* locally.

## Network

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `http_idle_timeout_ms` | integer | `60000` | Idle timeout for LLM HTTP streams, in milliseconds; minimum 0 |
| `websocket_connect_timeout_ms` | integer | – | WebSocket connect/open handshake timeout, in milliseconds |
| `http_proxy.url` | string | – | Proxy URL used for both HTTP and HTTPS; overrides environment variables |
| `http_proxy.no_proxy` | string | – | Comma-separated hosts excluded from proxying |
| `http_proxy.headers` | object | – | Extra headers sent to the proxy, e.g. for authentication |

```json
{
  "http_proxy": {
    "url": "http://127.0.0.1:7890",
    "no_proxy": "localhost,127.0.0.1,.internal",
    "headers": { "Proxy-Authorization": "$PROXY_TOKEN" }
  }
}
```

`http_proxy.url` and every value in `http_proxy.headers` support secret indirection: a literal value, `$ENV_VAR` to read an environment variable, or `!command` to run a command and use its output. Each is resolved once and cached. See [HTTP Proxy](http-proxy.md).

## Terminal & Execution

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `terminal.shell_path` | string | system shell | Shell binary used for command execution |
| `terminal.shell_command_prefix` | string | – | Lines prepended inside the shell before each command |
| `tool_timeout_seconds` | number | `120.0` | Per-tool-call timeout. `null` disables the timeout |
| `max_parallel_tool_calls` | integer | `10` | Maximum tool calls executed concurrently. Must be ≥ 1, or `null` for unlimited |
| `event_handler_timeout_seconds` | number | `10.0` | Timeout for a single event/hook handler. `null` disables the timeout |

```json
{
  "terminal": {
    "shell_path": "/opt/homebrew/bin/bash",
    "shell_command_prefix": "shopt -s expand_aliases"
  },
  "tool_timeout_seconds": 300,
  "max_parallel_tool_calls": 4
}
```

The three engine-level keys leave the corresponding `EngineOptions` default in place when unset. See [Engine](engine.md).

## Startup & Telemetry

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `quiet_startup` | boolean | `false` | Suppress the startup notice |
| `telemetry` | boolean | `true` | Send one anonymous, version-only install/update count. **Global scope only** |

## Project Trust

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `project_trust` | string | `"ask"` | Trust policy: `"ask"`, `"always"`, or `"never"` |

Trust gates everything a project directory can contribute: `.tau/settings.json`, project extensions, project context files (`AGENTS.md`, `CLAUDE.md`), and project skills. Until a project is trusted, `SettingsManager` merges an empty `Settings()` for the project scope — the file is not even read.

Granting trust mid-session re-reads `.tau/settings.json` and recomputes the merged view immediately; revoking it discards the project scope.

> **Security:** Project settings can load and execute project-local extension code. Only trust directories you control.

## Extensions

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `extensions.enabled` | boolean | `true` | Global on/off switch for all extensions |
| `extensions.list` | array | – | Per-extension entries |

Each entry in `extensions.list`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | *required* | Path to the extension file or directory |
| `name` | string | – | Display name |
| `enabled` | boolean | `true` | Enable this specific extension |
| `source` | string | – | Where the extension came from (`builtin`, `project`, `global`, `package`, …) |
| `author` | string | – | Author metadata |
| `settings` | object | – | Extension-specific configuration, exposed to the extension as `tau.config` |

```json
{
  "extensions": {
    "enabled": true,
    "list": [
      {
        "path": "~/.tau/extensions/my_ext.py",
        "name": "My Extension",
        "enabled": true,
        "settings": { "api_key": "$MY_API_KEY", "verbose": true }
      }
    ]
  }
}
```

An entry missing a `path` is dropped on load. Unlike other settings, the extension list is read from **both** scopes at runtime rather than only the merged view, so global and project extensions both load. See [Extension Settings](extension-settings.md) and [Extensions](extensions.md).

## Packages

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `packages.list` | array | – | Packages installed into the tau-managed venv |

Each entry in `packages.list`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | *required* | Install source, e.g. `"pypi:name==1.0"`, `"git+https://…"`, or a local path |
| `name` | string | *required* | Normalized package name |
| `version` | string | – | Installed version, if known |
| `installed_path` | string | – | Path to the package directory inside the venv |
| `enabled` | boolean | `true` | Load resources from this package |
| `extensions` | string[] | – | Restrict which extensions to load from the package |
| `skills` | string[] | – | Restrict which skills to load |
| `prompts` | string[] | – | Restrict which prompts to load |
| `themes` | string[] | – | Restrict which themes to load |
| `index_url` | string | – | Custom package index URL |
| `extra_index_urls` | string[] | – | Additional index URLs |

Entries missing `source` or `name` are dropped on load. This block is normally managed by tau's package commands rather than edited by hand.

## Full Example

```json
{
  "model": {
    "text": { "id": "claude-sonnet-4-6", "provider": "anthropic" }
  },
  "thinking_level": "medium",
  "thinking_budgets": { "medium": 8192 },
  "theme": "tokyo-night",
  "show_thinking": true,
  "picker_max_visible": 12,
  "steering_mode": "one_at_a_time",
  "session_dir": "~/work/tau-sessions",
  "compaction": {
    "enabled": true,
    "reserve_tokens": 16384,
    "keep_recent_tokens": 20000
  },
  "retry": { "enabled": true, "max_retries": 5 },
  "image": { "auto_resize": true, "block_images": false },
  "http_idle_timeout_ms": 60000,
  "terminal": { "shell_path": "/opt/homebrew/bin/bash" },
  "tool_timeout_seconds": 300,
  "project_trust": "ask",
  "quiet_startup": false,
  "telemetry": true,
  "extensions": { "enabled": true }
}
```

## Recovering a Corrupt Settings File

A malformed file does not stop tau from starting. Two failure modes are handled separately:

1. **Total parse failure** — invalid JSON, or a root that is not an object. The whole scope loads as defaults.
2. **Partial failure** — the file parses, but one field has the wrong shape (a string where an object was expected). Only that field resets to its default; every other field survives, and the issue is recorded.

Run `tau doctor` to list what was dropped, and `tau doctor --fix` to heal. Healing backs up the original alongside itself as `settings.json.corrupt-<timestamp>` before rewriting, so nothing is lost.

Unknown keys are ignored silently — an old key left in the file is harmless.

## Next Steps

- [Extension Settings](extension-settings.md) — per-extension configuration and `/settings` integration
- [Themes](themes.md) — theme format and color tokens
- [Keybindings](keybindings.md) — keyboard shortcuts
- [Sessions](sessions.md) — session storage and resumption
- [HTTP Proxy](http-proxy.md) — proxy configuration in depth
</content>
</invoke>
