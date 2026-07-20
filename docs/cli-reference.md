# CLI Reference

Every command-line option, subcommand, and run mode Tau exposes. For day-to-day interactive workflows, see [Usage Guide](usage.md).

## Table of Contents

- [Synopsis](#synopsis)
- [Global Options](#global-options)
- [Run Modes](#run-modes)
- [Model Selection](#model-selection)
- [Session Options](#session-options)
- [File Arguments](#file-arguments)
- [Subcommands](#subcommands)
- [RPC Mode](#rpc-mode)
- [Environment Variables](#environment-variables)
- [Exit Codes](#exit-codes)

## Synopsis

```bash
tau [OPTIONS] [@FILE...]              # interactive, or non-interactive with --prompt
tau COMMAND [ARGS]...                 # subcommand: auth, doctor, install, ...
```

Tau takes no positional message argument. Supply prompts with `--prompt`/`-p`, piped stdin, or `@file` arguments.

## Global Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--version` | `-v` | | Print the installed version and exit |
| `--help` | `-h` | | Show help and exit |
| `--debug` | `-d` | off | Enable debug logging |
| `--startup` | | off | Print per-phase startup timing to stderr (settings, model/LLM, session manager, resources, extensions, agent) |
| `--cwd PATH` | `-c` | current dir | Set the working directory before starting |
| `--prompt TEXT` | `-p` | | Run a single prompt non-interactively |
| `--output-format` | `-f` | `text` | Non-interactive output format: `text` or `json` |
| `--quiet` | `-q` | off | Hide the spinner in non-interactive mode |
| `--provider NAME` | | from settings | Provider to use, e.g. `anthropic`, `openai`, `groq` |
| `--model ID` | | from settings | Model ID, or `provider/model` shorthand |
| `--base-url URL` | | provider default | Override the provider base URL for this run only |
| `--effort LEVEL` | | model default | Thinking/reasoning effort for this run |
| `--theme NAME` | `-t` | `dark` | UI theme; builtins are `dark` and `light` |
| `--system TEXT` | `-s` | generated | Replace the generated system prompt completely |
| `--tools NAMES` | | all | Comma-separated allowlist of tool names |
| `--resume [ID]` | `-r` | | Resume the most recent session, or a specific one by ID |
| `--fork ID` | | | Fork a session by ID into a new session |
| `--session-dir PATH` | | `~/.tau/sessions` | Session storage directory |
| `--name NAME` | | | Session display name |
| `--ephemeral` | `-e` | off | Do not save this session to disk |
| `--print` | | | Shorthand for `--mode print` |
| `--mode MODE` | | resolved | `interactive`, `print`, `json`, or `rpc` |
| `--no-context-files` | `-nc` | off | Disable `AGENTS.md` and `CLAUDE.md` discovery |
| `--approve` | `-a` | off | Trust project-local files (extensions, settings, context files) |
| `--no-approve` | `-na` | off | Do not trust project-local files |

> **`-c` is `--cwd`, not "continue".** Use `-r` / `--resume` to continue a session.

`--effort` accepts `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`. The value is not persisted and is clamped to what the selected model actually supports.

`--tools` restricts the agent to the named built-in tools. Available names: `read`, `write`, `edit`, `terminal`, `glob`, `grep`, `ls`.

```bash
tau --tools read,grep,glob,ls          # read-only agent; cannot write or run commands
tau --tools read,edit,write            # file edits only; no shell access
```

## Run Modes

Tau resolves the mode in this order:

1. An explicit `--mode` always wins.
2. Otherwise, if `--prompt` is given: `json` when `--output-format json`, else `print`.
3. Otherwise, if `--print` is passed **or stdout is not a TTY**: `print`.
4. Otherwise: `interactive`.

Step 3 means Tau automatically switches to print mode when its output is piped or redirected.

| Mode | Flag | Description |
|------|------|-------------|
| Interactive | default | Full terminal UI |
| Print | `--print`, `-p TEXT` | Run one prompt, print the reply, exit |
| JSON | `--mode json`, `-p TEXT -f json` | Emit lifecycle events as JSON lines |
| RPC | `--mode rpc` | Bidirectional JSON-lines protocol over stdin/stdout |

### Interactive

```bash
tau                                    # start in the current directory
tau --cwd ~/projects/api               # start elsewhere
tau --provider anthropic               # pick a provider
tau --model claude-sonnet-4-6          # pick a model
tau --theme light --effort high        # theme and reasoning effort
```

### Print mode

Runs one prompt, prints the assistant's text to stdout, and exits.

```bash
tau --print "Summarize this repo"
tau --prompt "Explain this file" @src/main.py
cat README.md | tau --print "Summarize this text"
tau --prompt "Compare these" @src/old.py @src/new.py --quiet
```

Piped stdin, `@file` contents, and the explicit prompt are concatenated in that order. If none of the three yields text, Tau exits with an error. A failed turn exits non-zero with the error message.

### JSON mode

Emits one JSON object per line for each lifecycle event, ending at `settled`.

```bash
tau --mode json --prompt "List the Python files"
tau --prompt "Audit this repo" -f json > events.jsonl
```

Events emitted in JSON mode:

| Event | Fields beyond `type` |
|-------|----------------------|
| `agent_start` | — |
| `agent_end` | `messages`, `reason` |
| `message_start` | `message` |
| `message_update` | `message` |
| `message_end` | `message` |
| `tool_execution_start` | `tool_call` |
| `tool_execution_end` | `tool_result` |
| `settled` | — |

Consume the stream until `settled`:

```bash
tau --mode json -p "Count the test files" | while read -r line; do
  echo "$line" | python -c 'import json,sys; print(json.load(sys.stdin)["type"])'
done
```

RPC mode emits a larger event set — see [Events](#events).

## Model Selection

### Provider/model shorthand

Pass `provider/model` as the `--model` value to set both at once:

```bash
tau --model groq/llama-3.3-70b-versatile
tau --model anthropic/claude-sonnet-4-6
tau --model openai/gpt-4o
```

An explicit `--provider` always overrides the provider inferred from the shorthand. When neither flag is given, Tau falls back to settings, then to the built-in default `anthropic/claude-sonnet-4-6`.

### Base URL override

`--base-url` points the resolved provider at a different endpoint for the current run — a proxy, gateway, or self-hosted deployment. It applies to whichever provider ends up in use, whether set with `--provider`, inferred from shorthand, or taken from settings; `--provider` is not required alongside it.

```bash
tau --base-url http://localhost:8000/v1 --provider vllm
tau --model groq/llama-3.3-70b-versatile --base-url https://gateway.internal/v1
tau --base-url https://proxy.example.com/v1        # applies to the saved/default model
```

The override is in-memory only. It is never written to `settings.json` or `auth.json`, and there is no persistent equivalent — pass it again on the next run.

## Session Options

```bash
tau --resume                           # continue the most recent session
tau --resume abc123                    # resume by session ID (substring match)
tau --fork abc123                      # fork that session into a new one
tau --ephemeral                        # temporary session; nothing written to disk
tau --name "release audit"             # set the display name at startup
tau --session-dir ./scratch-sessions   # store sessions outside ~/.tau/sessions
```

`--resume` takes an optional value: bare `--resume` continues the most recent session, while `--resume ID` matches a session file whose name contains `ID`. If several match, the most recently modified wins.

`--resume` and `--fork` cannot be used together — Tau exits with an error.

## File Arguments

Prefix a path with `@` to attach its contents to the prompt. Tau rewrites these into `--file` arguments before parsing, so they may appear anywhere on the command line.

```bash
tau --print "Answer this" @prompt.md
tau -p "Review these files" @src/app.py @tests/test_app.py
```

Attached files are wrapped as `<file path="...">…</file>` blocks in the message. The path must exist and be a file, not a directory.

## Subcommands

```bash
tau auth      # manage provider credentials
tau doctor    # diagnose configuration, credentials, and models
tau install   # install a package (extension, skill, theme)
tau remove    # remove an installed package
tau list      # list installed packages
tau update    # update Tau itself or an extension package
```

### `tau auth`

Manage credentials in `~/.tau/auth.json`.

| Command | Arguments | Description |
|---------|-----------|-------------|
| `tau auth list` | | List stored credentials with masked keys |
| `tau auth status` | | Show per-provider credential state, including environment fallbacks |
| `tau auth set` | `PROVIDER KEY` | Store an API key |
| `tau auth unset` | `PROVIDER` | Remove stored credentials |
| `tau auth login` | `PROVIDER` | Run an OAuth subscription login flow |
| `tau auth logout` | `PROVIDER` | Remove an OAuth credential |

```bash
tau auth set anthropic sk-ant-...      # store a key
tau auth status                        # verify what Tau resolves
tau auth login github-copilot          # OAuth device flow
```

### `tau doctor`

Diagnoses settings and auth file integrity, credential status, model and provider resolution, extensions, session storage, logs, environment, and installed packages. Each check reports pass/warn/fail; the command exits non-zero if anything failed.

| Option | Description |
|--------|-------------|
| `--json` | Output machine-readable JSON |
| `--fix` | Apply safe, reversible repairs |

```bash
tau doctor                             # human-readable report
tau doctor --json                       # machine-readable
tau doctor --fix                        # repair: refresh expired OAuth tokens,
                                        #   remove dangling extension entries,
                                        #   quarantine corrupt sessions to .corrupt/
```

`--fix` never rewrites `settings.json` or `auth.json` directly, and never reinstalls packages.

### `tau install`

Installs a package as a Tau extension source.

| Option | Description |
|--------|-------------|
| `--local` | Install to project scope (`.tau/venv/`) instead of global (`~/.tau/venv/`) |
| `--index-url URL` | Base URL of a private Python package index |
| `--extra-index-url URL` | Additional index URL; repeatable |

Accepted `SOURCE` formats:

| Format | Example |
|--------|---------|
| PyPI, latest | `pypi:my-extension` |
| PyPI, pinned | `pypi:my-extension==1.2.3` |
| Git URL | `git+https://github.com/user/repo.git` |
| Local path | `./my-extension` or `/abs/path` |
| Archive URL | `https://example.com/pkg.whl` |

```bash
tau install pypi:tau-web-search                 # global install from PyPI
tau install ./my-extension --local              # project-scoped, from a local dir
tau install pypi:internal-ext --index-url https://pypi.internal/simple
```

### `tau remove`

```bash
tau remove my-extension                # remove from global scope
tau remove my-extension --local        # remove from project scope
```

### `tau list`

```bash
tau list                               # global packages
tau list --local                       # project-scoped packages only
tau list --all                         # both global and project packages
```

### `tau update`

With no arguments, updates Tau itself using whichever installer manages the current install. With a `NAME`, updates that extension package.

| Option | Description |
|--------|-------------|
| `--all` | Update Tau and all extension packages |
| `--local` | Update in project scope instead of global |

```bash
tau update                             # update Tau itself
tau update my-extension                # update one package
tau update --all                       # update Tau and every package
tau update my-extension --local        # update a project-scoped package
```

`NAME` cannot be combined with `--all`.

## RPC Mode

A bidirectional JSON-lines protocol for IDE extensions and programmatic clients.

```bash
tau --mode rpc
```

This section covers the CLI surface. For the full protocol — every command and event,
the handshake, error handling, and a complete client — see [RPC Mode](rpc.md).

### Framing

Records are delimited by `\n` (LF); each record is one complete JSON object. When parsing stdout, split on `\n` and strip an optional trailing `\r`. Output is flushed immediately after each record.

### Startup

Immediately after the runtime initializes, Tau emits one `ready` line:

```json
{"type": "ready", "sessionId": "abc123", "cwd": "/path/to/project"}
```

Both fields may be `null` — `sessionId` is null in ephemeral mode.

### Commands

Send one JSON object per line on stdin. Every command accepts an optional `id`, echoed back on the response.

```json
{"type": "prompt", "id": "1", "message": "Explain this code"}
{"type": "abort"}
{"type": "get_state", "id": "2"}
```

### Responses

Every command emits exactly one response line — with one exception, `extension_ui_response`, which emits nothing.

```json
{"type": "response", "command": "prompt",    "id": "1", "success": true}
{"type": "response", "command": "get_state", "id": "2", "success": true, "data": {"isStreaming": false, "sessionId": "abc123"}}
{"type": "response", "command": "set_model", "success": false, "error": "Model not found: bad/model"}
```

Unparseable input yields:

```json
{"type": "response", "command": "parse", "success": false, "error": "Failed to parse command: ..."}
```

An unrecognized `type` yields `"Unknown command type: '<x>'"`.

### Command Reference

#### Prompting

| Command | Key fields | Description |
|---------|-----------|-------------|
| `prompt` | `message` (required), `streamingBehavior?` | Send a user prompt. If the agent is already streaming, `streamingBehavior` is required — omitting it returns an error |
| `steer` | `message` (required) | Queue a steering message; errors with "No active agent" if idle |
| `follow_up` | `message` (required) | Queue a follow-up message |
| `abort` | — | Cancel the current agent turn |
| `new_session` | — | Start a fresh session; `data: {cancelled}` |

`streamingBehavior` is `"steer"` (delivered after the current turn's tool calls, before the next LLM call) or `"followUp"` (delivered only when the agent fully stops).

#### State

| Command | Response `data` |
|---------|----------------|
| `get_state` | `{model: {id, provider} \| null, thinkingLevel, isStreaming, isCompacting, sessionFile, sessionId, autoCompactionEnabled, messageCount, pendingMessageCount}` |
| `get_messages` | `{messages: [{role, text}]}` |

> `isCompacting` and `pendingMessageCount` are currently always `false` and `0` respectively.

#### Model and thinking

| Command | Key fields | Response `data` |
|---------|-----------|----------------|
| `set_model` | `modelId` (required), `provider?` | `{id, provider}` or `null` |
| `cycle_model` | — | `{model: {id, provider}}`, or `null` if only one model |
| `get_available_models` | — | `{models: [{id, provider, name, contextWindow}]}` |
| `set_thinking_level` | `level` (required) | — |
| `cycle_thinking_level` | — | `{level}`, or `null` if the model has no thinking support |

`level` is one of `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.

#### Queue modes

| Command | Key fields |
|---------|-----------|
| `set_steering_mode` | `mode: "all" \| "one-at-a-time"` |
| `set_follow_up_mode` | `mode: "all" \| "one-at-a-time"` |

#### Compaction and retry

| Command | Key fields | Description |
|---------|-----------|-------------|
| `compact` | `customInstructions?` | `data: {summary, firstKeptEntryId, tokensBefore}` or `null` |
| `set_auto_compaction` | `enabled: bool` | Enable or disable automatic compaction |
| `set_auto_retry` | `enabled: bool` | Enable or disable automatic retry on transient errors |
| `abort_retry` | — | Cancel an in-progress retry delay |

#### Shell

| Command | Key fields | Description |
|---------|-----------|-------------|
| `terminal` | `command` (required), `excludeFromContext?` | Run a shell command; output is added to the next LLM context unless excluded |
| `abort_terminal` | — | Abort a running terminal subprocess |

#### Session

| Command | Key fields | Response `data` |
|---------|-----------|----------------|
| `get_session_stats` | — | `{sessionFile, sessionId, userMessages, assistantMessages, totalMessages, cwd, contextUsage: {tokens, contextWindow, percent} \| null}` |
| `switch_session` | `sessionPath` (required) | `{cancelled}` |
| `fork` | `entryId` (required), `position?: "before" \| "at"` | `{text, cancelled}` |
| `clone` | — | `{cancelled}` |
| `get_fork_messages` | — | `{messages: [{entryId, text}]}` — user messages available for forking |
| `get_last_assistant_text` | — | `{text: string \| null}` |
| `set_session_name` | `name` | — |
| `get_commands` | — | `{commands: [{name, description, source}]}`; `source` is `"extension"`, `"prompt"`, or `"skill"` |
| `export_html` | — | Always fails: `"export_html is not supported in this build"` |

### Events

Agent lifecycle events stream alongside responses. Events never carry an `id`; only responses do.

| Event | Key fields |
|-------|-----------|
| `agent_start` | — |
| `agent_end` | `messages`, `reason` |
| `turn_start` | `turn_index`, `timestamp` |
| `turn_end` | `turn_index`, `message`, `tool_results` |
| `message_start` | `message` |
| `message_update` | `message` |
| `message_end` | `message` |
| `tool_execution_start` | `tool_call` |
| `tool_execution_update` | `partial_tool_result` |
| `tool_execution_end` | `tool_result` |
| `agent_error` | `error` |
| `compaction_start` | `manual`, `reason`, `will_retry` |
| `compaction_end` | `manual`, `tokens_before`, `summary_length`, `from_extension`, `reason`, `will_retry` |
| `queue_update` | `queue` (`"steering"` or `"followup"`), `message`, `messages` |
| `settled` | — |

A typical round trip:

```text
→ {"type":"prompt","id":"1","message":"hello"}
← {"type":"agent_start"}
← {"type":"message_start","message":{...}}
← {"type":"message_update","message":{...}}
← {"type":"message_end","message":{...}}
← {"type":"agent_end","messages":[...],"reason":"completed"}
← {"type":"settled"}
← {"type":"response","command":"prompt","id":"1","success":true}
```

### Extension UI

Extension dialog methods emit an `extension_ui_request` on stdout and block until the client replies. Request ids are `ui_1`, `ui_2`, and so on.

Blocking methods:

```json
{"type": "extension_ui_request", "id": "ui_1", "method": "select",  "title": "Pick a branch", "options": ["main", "dev"]}
{"type": "extension_ui_request", "id": "ui_2", "method": "confirm", "title": "Delete file?", "message": "This cannot be undone."}
{"type": "extension_ui_request", "id": "ui_3", "method": "input",   "title": "Enter a name", "placeholder": "my-session"}
{"type": "extension_ui_request", "id": "ui_4", "method": "editor",  "title": "Edit prompt", "prefill": "existing text"}
```

Client replies with a matching `id`:

```json
{"type": "extension_ui_response", "id": "ui_1", "value": "main"}
{"type": "extension_ui_response", "id": "ui_2", "confirmed": true}
{"type": "extension_ui_response", "id": "ui_3", "value": "my-session"}
{"type": "extension_ui_response", "id": "ui_4", "cancelled": true}
```

A truthy `cancelled` resolves the dialog as `None`. Otherwise, a present `confirmed` key resolves to the confirmation result; failing that, `value` is used.

Fire-and-forget methods expect no reply:

```json
{"type": "extension_ui_request", "id": "ui_5", "method": "notify",          "message": "Done!", "notifyType": "info"}
{"type": "extension_ui_request", "id": "ui_6", "method": "setStatus",       "statusKey": "my-ext", "statusText": "running…"}
{"type": "extension_ui_request", "id": "ui_7", "method": "setWidget",       "widgetKey": "banner", "widgetLines": ["─ my ext ─"], "widgetPlacement": "aboveEditor"}
{"type": "extension_ui_request", "id": "ui_8", "method": "setTitle",        "title": "tau – my project"}
{"type": "extension_ui_request", "id": "ui_9", "method": "set_editor_text", "text": "prefilled text"}
```

`notifyType` is `"info"` (default), `"warning"`, or `"error"`. `widgetPlacement` is `"aboveEditor"` or `"belowEditor"`. Omit `statusText` or `widgetLines` to clear the slot.

### Shutdown

Tau shuts down on EOF on stdin, or on SIGTERM/SIGHUP, which abort the current agent turn first. Signal handling is skipped on platforms that lack these signals.

### Example client

```python
import json
import subprocess

proc = subprocess.Popen(
    ["tau", "--mode", "rpc", "--ephemeral"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)


def send(cmd: dict) -> None:
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()


# Wait for the ready handshake
ready = json.loads(proc.stdout.readline())
print("session:", ready["sessionId"])

send({"type": "prompt", "id": "1", "message": "Say hello in one sentence."})

# Stream events until the agent settles
for line in proc.stdout:
    event = json.loads(line.rstrip("\r\n"))
    if event["type"] == "message_update":
        pass  # incremental chunk
    elif event["type"] == "settled":
        break

send({"type": "get_last_assistant_text", "id": "2"})
resp = json.loads(proc.stdout.readline())
print(resp["data"]["text"])

proc.stdin.close()
proc.wait()
```

## Environment Variables

Tau reads no `TAU_`-prefixed configuration variables other than `TAU_PROFILE`. Config, session, and log locations are fixed under `~/.tau/` and cannot be relocated by environment variable — use `--session-dir` for session files.

| Variable | Effect |
|----------|--------|
| `<PROVIDER_ID>_API_KEY` | API key for any provider; the name is the provider id uppercased plus `_API_KEY` |
| `TAU_PROFILE` | Set to `1` to collect aggregate component timings |
| `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` | Proxy configuration, read case-insensitively; `settings.json` takes precedence |
| `GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT` | Google Vertex project id |
| `GOOGLE_CLOUD_LOCATION` | Google Vertex region |
| `GOOGLE_CLOUD_API_KEY` | Google Vertex API key when not using ADC |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a Google service-account JSON file |
| `CLAUDE_CONFIG_DIR` | Windows only: where to find Claude Code credentials (default `~/.claude`) |

Provider and model can be set permanently in `settings.json` — see [Settings](settings.md).

### Profiling

With `TAU_PROFILE=1`, Tau writes a report to `~/.tau/logs/profile-<pid>-<timestamp>.log` when the process exits. It covers startup phases, per-extension discovery/load/import/registration spans, TUI base rendering, overlay rendering and blitting, tool calls, and session persistence.

```bash
TAU_PROFILE=1 tau                      # profile an interactive run
TAU_PROFILE=1 tau -p "hello"           # profile a one-shot run
```

For a single-shot phase breakdown without the full profiler, use `--startup`:

```bash
tau --startup                          # per-phase timings to stderr
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error, missing required argument, or a failed agent turn |
| 2 | Click usage error (unknown flag, bad option value) |

`tau doctor` exits non-zero when any check fails.

## Next Steps

- [Usage Guide](usage.md) — interactive mode and slash commands
- [Installation](installation.md) — setup and credential precedence
- [Settings](settings.md) — persistent configuration
- [Sessions](sessions.md) — session storage and branching
- [Extensions](extensions.md) — building extensions that use the RPC UI surface
