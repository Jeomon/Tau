# CLI Reference

## Usage

```bash
tau [OPTIONS] [MESSAGE]
```

Use `--prompt TEXT` for a non-interactive prompt. Piped stdin can supply or
augment it.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--prompt` | `-p` | Run a non-interactive prompt |
| `--provider` | | Provider to use, e.g. `anthropic`, `openai`, `groq` |
| `--model` | | Model ID, or `provider/model` shorthand (e.g. `groq/llama-3.3-70b-versatile`) |
| `--theme` | `-t` | UI theme: `default`, `dracula`, `nord`, `gruvbox`, `catppuccin`, `ayu-dark`, `everforest`, `horizon`, `kanagawa`, `material-ocean`, `monokai`, `night-owl`, `one-dark`, `rose-pine`, `solarized-dark`, `tokyo-night`, or a custom name |
| `--resume [ID]` | `-r` | Resume the most recent or a specified session |
| `--fork ID` | | Fork a specified session at startup |
| `--session-dir PATH` | | Override session storage |
| `--name NAME` | | Set the session display name at startup |
| `--ephemeral` | `-e` | Don't save this session to disk |
| `--approve` | `-a` | Trust project-local files (extensions, settings, context files) for this run |
| `--no-approve` | `-na` | Don't trust project-local files for this run |
| `--no-context-files` | `-nc` | Disable AGENTS.md and CLAUDE.md discovery and loading |
| `--print` | | Print mode — run `MESSAGE` and exit (shorthand for `--mode print`) |
| `--mode` | | Run mode: `interactive` (default), `print`, `json`, `rpc` |
| `--help` | `-h` | Show help message |

## Run Modes

## Interactive Commands

`/watch <url> [question]` loads metadata and available English captions for a
public video through `yt-dlp`, then sends them to the active model. Install
`yt-dlp` separately and ensure it is available on `PATH`. Video frames and
audio transcription are not performed.

### Interactive (default)

Start the full terminal UI:

```bash
tau
tau --provider anthropic
tau --model claude-sonnet-4-6
```

### Print mode

Run a single prompt, print the response, and exit:

```bash
tau --prompt "Summarize this repo"
cat README.md | tau --print --prompt "Summarize this text"
tau --prompt "Compare these files" @src/old.py @src/new.py
```

Piped text, attached file contents, and the explicit prompt are combined in
that order.

### JSON mode

Emit a JSON event stream on stdout:

```bash
tau --mode json "Your prompt"
```

Each line is a JSON object representing a lifecycle event (`agent_start`, `message_end`, etc.).

### RPC mode

JSON-lines bidirectional protocol for IDE extensions and programmatic clients:

```bash
tau --mode rpc
```

#### Framing

Records are delimited by `\n` (LF). Each record is a complete JSON object. When parsing stdout, split on `\n` only and strip an optional trailing `\r` from each line.

#### Startup

Immediately after the runtime initialises, tau emits a single `ready` line:

```json
{"type": "ready", "sessionId": "abc123", "cwd": "/path/to/project"}
```

#### Commands (stdin)

Send one JSON object per line. Include an optional `"id"` field to correlate the response:

```json
{"type": "prompt", "id": "1", "message": "Explain this code"}
{"type": "abort"}
{"type": "get_state", "id": "2"}
```

#### Responses (stdout)

Every command emits exactly one response with `type: "response"`. Data lives in the `data` field:

```json
{"type": "response", "command": "prompt",    "id": "1", "success": true}
{"type": "response", "command": "get_state", "id": "2", "success": true, "data": {"isStreaming": false, "sessionId": "abc123", ...}}
```

On failure:

```json
{"type": "response", "command": "set_model", "success": false, "error": "Model not found: bad/model"}
```

#### Events (stdout)

Agent lifecycle events stream alongside responses:

```json
{"type": "agent_start"}
{"type": "turn_start", "turn_index": 0}
{"type": "message_update", "message": {...}}
{"type": "message_end", "message": {...}}
{"type": "agent_end", "messages": [...], "reason": "completed"}
{"type": "settled"}
```

Events do **not** have an `id` field. Only responses do.

---

#### Command reference

##### Prompting

| Command | Key fields | Description |
|---------|-----------|-------------|
| `prompt` | `message` (required) | Send a user prompt. If the agent is streaming, you must also pass `streamingBehavior: "steer"` or `"followUp"` — omitting it returns an error |
| `steer` | `message` (required) | Queue a steering message to redirect the current turn |
| `follow_up` | `message` (required) | Queue a follow-up message to deliver after the current turn finishes |
| `abort` | — | Cancel the current agent turn |
| `new_session` | `parentSession?` | Start a fresh session; `data.cancelled` is true if an extension blocked it |

**`streamingBehavior` values for `prompt`:**

| Value | Effect |
|-------|--------|
| `"steer"` | Delivered after the current assistant turn finishes its tool calls, before the next LLM call |
| `"followUp"` | Delivered only when the agent fully stops |

##### State

| Command | Response `data` |
|---------|----------------|
| `get_state` | `{model, thinkingLevel, isStreaming, isCompacting, steeringMode, followUpMode, sessionFile, sessionId, sessionName?, autoCompactionEnabled, messageCount, pendingMessageCount}` |
| `get_messages` | `{messages: [{role, text}]}` |

##### Model

| Command | Key fields | Response `data` |
|---------|-----------|----------------|
| `set_model` | `modelId` (required), `provider?` | Full model object |
| `cycle_model` | — | `{model}` — next model, or `null` if only one model |
| `get_available_models` | — | `{models: [{id, provider, name, contextWindow}]}` |

##### Thinking

| Command | Key fields | Response `data` |
|---------|-----------|----------------|
| `set_thinking_level` | `level` — `"off"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"` | — |
| `cycle_thinking_level` | — | `{level}` — next level, or `null` if model doesn't support thinking |

##### Queue modes

| Command | Key fields | Description |
|---------|-----------|-------------|
| `set_steering_mode` | `mode: "all" \| "one-at-a-time"` | How many queued steering messages to drain per turn |
| `set_follow_up_mode` | `mode: "all" \| "one-at-a-time"` | How many queued follow-up messages to drain per completion |

##### Compaction & retry

| Command | Key fields | Description |
|---------|-----------|-------------|
| `compact` | `customInstructions?` | Manually compact context; `data` contains `{summary, firstKeptEntryId, tokensBefore}` |
| `set_auto_compaction` | `enabled: bool` | Enable or disable automatic compaction |
| `set_auto_retry` | `enabled: bool` | Enable or disable automatic retry on transient errors |
| `abort_retry` | — | Cancel an in-progress retry delay |

##### Messages

| Command | Key fields | Description |
|---------|-----------|-------------|
| `clear` | — | Clear all messages from the current session |

##### Shell

| Command | Key fields | Description |
|---------|-----------|-------------|
| `bash` | `command` (required), `excludeFromContext?` | Run a shell command; output is added to the next LLM context |
| `abort_bash` | — | Abort a running bash subprocess |

##### Session

| Command | Key fields | Response `data` |
|---------|-----------|----------------|
| `get_session_stats` | — | `{sessionFile, sessionId, userMessages, assistantMessages, totalMessages, cwd, contextUsage?}` |
| `switch_session` | `sessionPath` (required) | `{cancelled: bool}` |
| `fork` | `entryId` (required), `position?: "before"\|"at"` | `{text, cancelled}` — text of the forked message |
| `clone` | — | `{cancelled: bool}` |
| `get_fork_messages` | — | `{messages: [{entryId, text}]}` — user messages available for forking |
| `get_last_assistant_text` | — | `{text: string \| null}` |
| `set_session_name` | `name` | — |
| `get_commands` | — | `{commands: [{name, description, source}]}` — `source`: `"extension"`, `"prompt"`, or `"skill"` |

---

#### Events reference

| Event | Key fields | Description |
|-------|-----------|-------------|
| `agent_start` | — | Agent begins processing |
| `agent_end` | `messages`, `reason` | Agent finishes; `reason`: `"completed"`, `"aborted"`, `"error"` |
| `turn_start` | `turn_index`, `timestamp` | New LLM inference turn |
| `turn_end` | `turn_index`, `message`, `tool_results` | Turn complete |
| `message_start` | `message` | Model starts streaming |
| `message_update` | `message` | Incremental chunk |
| `message_end` | `message` | Response fully received |
| `tool_execution_start` | `tool_call` | Tool begins executing |
| `tool_execution_update` | `partial_tool_result` | Streaming tool progress |
| `tool_execution_end` | `tool_result` | Tool finishes |
| `agent_error` | `error` | Unrecoverable engine error |
| `compaction_start` | `manual` | Compaction begins |
| `compaction_end` | `manual`, `tokens_before`, `summary_length`, `from_extension` | Compaction finishes |
| `queue_update` | `queue`, `message`, `messages` | Steering/follow-up queue changed; `queue`: `"steering"` or `"followup"` |
| `settled` | — | Agent is fully idle — no more queued turns |

---

#### Extension UI over RPC

Extension dialog methods (`select`, `confirm`, `input`, `editor`) emit an `extension_ui_request` on stdout and block until the client responds. Fire-and-forget methods (`notify`, `setStatus`, `setWidget`, `setTitle`, `set_editor_text`) emit without waiting.

**tau → client (dialog):**

```json
{"type": "extension_ui_request", "id": "ui_1", "method": "select",  "title": "Pick a branch", "options": ["main", "dev"]}
{"type": "extension_ui_request", "id": "ui_2", "method": "confirm", "title": "Delete file?", "message": "This cannot be undone."}
{"type": "extension_ui_request", "id": "ui_3", "method": "input",   "title": "Enter a name", "placeholder": "my-session"}
{"type": "extension_ui_request", "id": "ui_4", "method": "editor",  "title": "Edit prompt",  "prefill": "existing text"}
```

**client → tau (response, `id` must match):**

```json
{"type": "extension_ui_response", "id": "ui_1", "value": "main"}
{"type": "extension_ui_response", "id": "ui_2", "confirmed": true}
{"type": "extension_ui_response", "id": "ui_3", "value": "my-session"}
{"type": "extension_ui_response", "id": "ui_4", "cancelled": true}
```

**tau → client (fire-and-forget, no response needed):**

```json
{"type": "extension_ui_request", "id": "ui_5", "method": "notify",    "message": "Done!", "notifyType": "info"}
{"type": "extension_ui_request", "id": "ui_6", "method": "setStatus",  "statusKey": "my-ext", "statusText": "running…"}
{"type": "extension_ui_request", "id": "ui_7", "method": "setWidget",  "widgetKey": "banner", "widgetLines": ["─ my ext ─"], "widgetPlacement": "aboveEditor"}
{"type": "extension_ui_request", "id": "ui_8", "method": "setTitle",   "title": "tau – my project"}
{"type": "extension_ui_request", "id": "ui_9", "method": "set_editor_text", "text": "prefilled text"}
```

`notifyType` is `"info"` (default), `"warning"`, or `"error"`. Omit `statusText` / `widgetLines` to clear the slot.

#### Error handling

Failed commands return `success: false`:

```json
{"type": "response", "command": "set_model", "success": false, "error": "Model not found: bad/model"}
```

JSON parse failures:

```json
{"type": "response", "command": "parse", "success": false, "error": "Failed to parse command: Unexpected token…"}
```

#### Signal handling

SIGTERM and SIGHUP abort the current agent turn and shut down cleanly.

#### Example client (Python)

```python
import subprocess, json

proc = subprocess.Popen(
    ["tau", "--mode", "rpc", "--ephemeral"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)

def send(cmd: dict) -> None:
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()

# Wait for ready
ready = json.loads(proc.stdout.readline())
print("session:", ready["sessionId"])

# Send a prompt
send({"type": "prompt", "id": "1", "message": "Say hello in one sentence."})

# Stream events until settled
for line in proc.stdout:
    event = json.loads(line.rstrip("\r\n"))
    if event["type"] == "message_update":
        pass  # incremental chunk
    elif event["type"] == "settled":
        break

# Get the final text
send({"type": "get_last_assistant_text", "id": "2"})
resp = json.loads(proc.stdout.readline())
print(resp["data"]["text"])

proc.stdin.close()
proc.wait()
```

## Provider / Model Shorthand

Use `provider/model` as the `--model` value to set both at once:

```bash
tau --model groq/llama-3.3-70b-versatile
tau --model anthropic/claude-sonnet-4-6
```

An explicit `--provider` always overrides the inferred provider.

## Session Options

```bash
tau --resume                                    # continue most recent session
tau --session abc123                            # resume by session ID
tau --session ~/.tau/sessions/proj/file.jsonl   # resume by file path
tau --ephemeral                                 # temporary session, nothing saved
```

## Subcommands

### `tau auth`

Manage provider credentials.

```bash
tau auth --help
```

## Environment Variables

| Variable | Effect |
|----------|--------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` | Google Gemini key |
| `<PROVIDER>_API_KEY` | API key for any provider (uppercased provider name) |

Provider and model can also be set permanently in `settings.json` — see [Settings](settings.md).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error / missing argument |
| 2 | Click usage error |

## Next Steps

- [Usage Guide](usage.md) — Interactive mode and slash commands
- [Settings](settings.md) — Persistent configuration
- [Installation](installation.md) — Setup
