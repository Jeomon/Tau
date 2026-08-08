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
- [Remote Mode](#remote-mode)
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
| `--prompt TEXT` | `-p` | | Run a prompt non-interactively. Repeat to send several in order |
| `--output-format` | `-f` | `text` | Non-interactive output format: `text` or `json` |
| `--json-events` | | `compact` | Event set for `json` output: `compact` or `full` |
| `--quiet` | `-q` | off | Hide the spinner in non-interactive mode |
| `--provider NAME` | | from settings | Provider to use, e.g. `anthropic`, `openai`, `groq` |
| `--model ID` | | from settings | Model ID, or `provider/model` shorthand |
| `--base-url URL` | | provider default | Override the provider base URL for this run only |
| `--effort LEVEL` | | model default | Thinking/reasoning effort for this run |
| `--theme NAME` | `-t` | `dark` | UI theme: any of the 17 built-ins, a custom theme, or `auto` to follow the terminal background. See [Themes](themes.md) |
| `--system TEXT` | `-s` | generated | Replace the generated system prompt completely |
| `--append-system-prompt TEXT` | | | Append text to the system prompt, generated or replaced |
| `--tools NAMES` | | all | Comma-separated allowlist of tool names |
| `--exclude-tools NAMES` | | | Comma-separated tool names to disable, applied after `--tools` |
| `--resume [ID]` | `-r` | | Resume the most recent session, or a specific one by ID |
| `--continue` | | | Resume the most recent session; alias for bare `--resume` |
| `--fork ID` | | | Fork a session by ID into a new session |
| `--session-dir PATH` | | `~/.tau/sessions` | Session storage directory |
| `--name NAME` | | | Session display name |
| `--ephemeral` | `-e` | off | Do not save this session to disk |
| `--print` | | | Shorthand for `--mode print` |
| `--mode MODE` | | resolved | `interactive`, `print`, `json`, `rpc`, or `remote` |
| `--socket PATH` | | session-named | Unix socket for `--mode remote` |
| `--no-context-files` | `-nc` | off | Disable `AGENTS.md` and `CLAUDE.md` discovery |
| `--approve` | `-a` | off | Trust project-local files (extensions, settings, context files) |
| `--no-approve` | `-na` | off | Do not trust project-local files |

> **`-c` is `--cwd`, not "continue".** Use `--continue`, `-r`, or bare `--resume` to continue a session.

`--effort` accepts `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`. The value is not persisted and is clamped to what the selected model actually supports.

`--tools` restricts the agent to the named built-in tools. Available names: `read`, `write`, `edit`, `terminal`, `glob`, `grep`, `ls`.

```bash
tau --tools read,grep,glob,ls          # read-only agent; cannot write or run commands
tau --tools read,edit,write            # file edits only; no shell access
tau --exclude-tools terminal           # everything except the shell
```

`--exclude-tools` is applied after `--tools`, so a name in both is disabled. Use it to
subtract from the default set without having to enumerate everything you want to keep.

## Run Modes

Tau resolves the mode in this order:

1. An explicit `--mode` always wins.
2. Otherwise, if `--prompt` is given: `json` when `--output-format json`, else `print`.
3. Otherwise, if `--print` is passed **or stdin or stdout is not a TTY**: `print`.
4. Otherwise: `interactive`.

Step 3 means Tau automatically switches to print mode when its output is piped or redirected, and when its input is. The interactive UI needs a TTY on both ends — it puts stdin into raw mode and paints stdout — so `echo "review this" | tau` runs headless with the piped text as the prompt. Pass `--mode interactive` to override the detection.

| Mode | Flag | Description |
|------|------|-------------|
| Interactive | default | Full terminal UI |
| Print | `--print`, `-p TEXT` | Run one prompt, print the reply, exit |
| JSON | `--mode json`, `-p TEXT -f json` | Emit lifecycle events as JSON lines |
| RPC | `--mode rpc` | Bidirectional JSON-lines protocol over stdin/stdout |
| Remote | `--mode remote` | Serve one session to several clients over a unix socket |

### Interactive

```bash
tau                                    # start in the current directory
tau --cwd ~/projects/api               # start elsewhere
tau --provider anthropic               # pick a provider
tau --model claude-sonnet-4-6          # pick a model
tau --theme light --effort high        # theme and reasoning effort
```

### Print mode

Runs the prompts, prints the assistant's final text to stdout, and exits.

```bash
tau --print "Summarize this repo"
tau --prompt "Explain this file" @src/main.py
cat README.md | tau --print "Summarize this text"
tau --prompt "Compare these" @src/old.py @src/new.py --quiet
tau -p "Read the tests" -p "Now list what they miss"
```

Piped stdin, `@file` contents, and the first prompt are concatenated in that order. If none of the three yields text, Tau exits with an error. A failed turn exits non-zero with the error message.

Repeating `--prompt` sends each one in turn against the same session, so a later prompt sees everything the earlier ones did. Each waits for the previous to settle. Piped stdin and `@file` contents attach to the first prompt only. This applies to `json` mode too, which emits one continuous event stream across all of them.

Both modes handle `SIGTERM` and `SIGHUP`: the running turn is aborted so tools stop and the session is written out, then Tau exits `143` or `129` respectively. `SIGINT` (Ctrl-C) is left to Python's normal `KeyboardInterrupt`.

### JSON mode

Emits one JSON object per line for each lifecycle event, ending at `settled`.

```bash
tau --mode json --prompt "List the Python files"
tau --prompt "Audit this repo" -f json > events.jsonl
```

JSON mode and RPC mode share their event pipeline, so anything documented under
[RPC Events](rpc.md#events) can appear here. Which of them actually arrive is
set by `--json-events`:

| `--json-events` | Emits |
|-----------------|-------|
| `compact` (default) | The streaming essentials — the ✓ rows below |
| `full` | Everything RPC sends |

| Event | `compact` | Fields beyond `type` |
|-------|:---------:|----------------------|
| `agent_start` | ✓ | — |
| `agent_end` | ✓ | `messages`, `reason` |
| `turn_start` | | `turn_index`, `timestamp` |
| `turn_end` | | `turn_index`, `message`, `tool_results` |
| `message_start` | ✓ | `message` |
| `message_update` | ✓ | `delta`, `thinking_delta` |
| `message_end` | ✓ | `message` |
| `message_rollback` | ✓ | `count` |
| `tool_execution_start` | ✓ | `tool_call` |
| `tool_execution_update` | | `partial_tool_result` |
| `tool_execution_end` | ✓ | `tool_result` |
| `tool_execution_failure` | | `tool_name`, … |
| `agent_error` | ✓ | `error` |
| `llm_retry` | | retry detail |
| `compaction_start` / `_end` / `_cancelled` / `_failure` | | see [RPC Events](rpc.md#events) |
| `queue_update` | | `queue`, `message`, `messages` |
| `terminal_execution` | | `message`, `streaming` |
| `terminal_output` | | `message` |
| `settled` | ✓ | — |

`message_update` carries only the text appended since the previous update, not
the message so far: `delta` for assistant text, `thinking_delta` for reasoning.
Either field is omitted when that stream did not advance, so an update that
only records a tool call carries neither. Concatenate the deltas to follow the
reply live, or ignore them and read the finished message from `message_end`.
When a block is rewritten rather than extended, the delta carries the whole new
text — compare against what you hold instead of appending blindly.

`message_rollback` retracts the last `count` committed messages. An interrupted
tool turn persists an assistant tool-call message and its result before the
abort lands, and both must be dropped; a consumer that mirrors the transcript
and ignores this event drifts out of sync with the session file. It is the one
event in `compact` that older consumers will not have seen — everything else
the shared pipeline can emit is behind `--json-events full`, but leaving this
one out would mean shipping a known way to corrupt a mirrored transcript.

Consume the stream until `settled`:

```bash
tau --mode json -p "Count the test files" | while read -r line; do
  echo "$line" | python -c 'import json,sys; print(json.load(sys.stdin)["type"])'
done
```

**stdout belongs to the protocol.** As in RPC mode, fd 1 is duplicated for the
stream itself and pointed at stderr, so a `print` from a tool, an extension or
a subprocess lands on stderr instead of corrupting a JSON line. Read stderr
separately, or discard it. Values that are not JSON-native are coerced rather
than dropped — enums become their value, `bytes` become base64, sets and tuples
become arrays, paths become strings — so a tool returning an image cannot take
its event off the stream.

RPC mode adds commands on stdin and a `ready` handshake; the event stream is
the same. See [RPC Mode](rpc.md).

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

`--base-url` points the resolved provider at a different endpoint for the current run: a proxy, gateway, or self-hosted deployment. It applies to whichever provider ends up in use, whether set with `--provider`, inferred from shorthand, or taken from settings; `--provider` is not required alongside it.

```bash
tau --base-url http://localhost:8000/v1 --provider vllm
tau --model groq/llama-3.3-70b-versatile --base-url https://gateway.internal/v1
tau --base-url https://proxy.example.com/v1        # applies to the saved/default model
```

The override is in-memory only. It is never written to `settings.json` or `auth.json`, and there is no persistent equivalent. Pass it again on the next run.

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

`--resume` and `--fork` cannot be used together. Tau exits with an error.

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

This section covers the CLI surface. For the full protocol (every command and event,
the handshake, error handling, and a complete client), see [RPC Mode](rpc.md).

### Framing

Records are delimited by `\n` (LF); each record is one complete JSON object. When parsing stdout, split on `\n` and strip an optional trailing `\r`. Output is flushed immediately after each record.

### Startup

Immediately after the runtime initializes, Tau emits one `ready` line:

```json
{
  "type": "ready",
  "protocolVersion": 1,
  "runtimeVersion": "0.9.3",
  "capabilities": {"toolCallBlocking": true, "interceptableEvents": ["..."], "projectTrust": true},
  "sessionId": "abc123",
  "cwd": "/path/to/project",
  "projectTrusted": false,
  "projectTrustSource": "undecided"
}
```

`sessionId` and `cwd` may be `null`: `sessionId` is null in ephemeral mode. `projectTrusted` says whether project-local code was loaded, and `projectTrustSource` how that was decided — `no-inputs`, `flag`, `policy`, `stored`, `undecided`, `session` or `default`.

`protocolVersion`, `runtimeVersion` and `capabilities` let a client feature-detect instead of pinning a version out of band; builds before 0.9.3 send none of them, so treat a missing `protocolVersion` as pre-negotiation. See [RPC mode](rpc.md#version-and-capabilities) for the bump policy, what each capability means, and the `trust` command that settles an undecided project.

### Commands

Send one JSON object per line on stdin. Every command accepts an optional `id`, echoed back on the response.

```json
{"type": "prompt", "id": "1", "message": "Explain this code"}
{"type": "abort"}
{"type": "get_state", "id": "2"}
```

### Responses

Every command emits exactly one response line, with one exception, `extension_ui_response`, which emits nothing.

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
| `prompt` | `message` (required), `streamingBehavior?` | Send a user prompt. If the agent is already streaming, `streamingBehavior` is required; omitting it returns an error |
| `steer` | `message` (required) | Queue a steering message; errors with "No active agent" if idle |
| `follow_up` | `message` (required) | Queue a follow-up message |
| `abort` | — | Cancel the current agent turn |
| `new_session` | — | Start a fresh session; `data: {cancelled}` |

`streamingBehavior` is `"steer"` (delivered after the current turn's tool calls, before the next LLM call) or `"followUp"` (delivered only when the agent fully stops).

#### State

| Command | Response `data` |
|---------|----------------|
| `get_state` | `{model: {id, provider} \| null, thinkingLevel, isStreaming, isCompacting, sessionFile, sessionId, autoCompactionEnabled, messageCount, pendingMessageCount, projectTrusted, projectTrustSource}` |
| `get_messages` | `{messages: [{role, text}]}` |
| `trust` | `{trusted, source, stored, storedPath, cwd, reloaded}`. Fields: `trusted?`, `remember?`, `forget?` |

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
| `get_fork_messages` | — | `{messages: [{entryId, text}]}`: user messages available for forking |
| `get_last_assistant_text` | — | `{text: string \| null}` |
| `set_session_name` | `name` | — |
| `get_commands` | — | `{commands: [{name, description, source}]}`; `source` is `"extension"`, `"prompt"`, or `"skill"` |
| `export_html` | `outputPath` (required) | `{path}`: the written HTML transcript |

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

## Remote Mode

Serves one running session over a unix socket so several clients can watch and drive it at once.

```bash
tau --mode remote                          # ~/.tau/remote/<session-id>.sock
tau --mode remote --socket /tmp/work.sock  # explicit path
```

Unlike `--mode rpc`, stdout is not a protocol stream — it prints the socket path and nothing else. Ctrl-C stops the server and removes the socket.

The command and event vocabulary is identical to RPC mode; only the framing differs (length-prefixed rather than newline-delimited). For the protocol, client API, and the rules governing broadcast and slow clients, see [Remote Access](remote.md).


## Environment Variables

Tau reads no `TAU_`-prefixed configuration variables other than `TAU_PROFILE`,
`TAU_OAUTH_CALLBACK_HOST` and `TAU_CACHE_RETENTION`. Config, session, and log
locations are fixed under `~/.tau/` and cannot be relocated by environment
variable. Use `--session-dir` for session files.

| Variable | Effect |
|----------|--------|
| `<PROVIDER_ID>_API_KEY` | API key for any provider; the name is the provider id uppercased plus `_API_KEY` |
| `TAU_PROFILE` | Set to `1` to collect aggregate component timings |
| `TAU_OAUTH_CALLBACK_HOST` | Bind address for OAuth callback servers; one host or a comma-separated list. Defaults to `127.0.0.1,::1`. See [Authentication](auth.md) |
| `TAU_CACHE_RETENTION` | Anthropic prompt-cache TTL: `none`, `short` (default, 5 min) or `long`. Unrecognised values fall back to `short` |
| `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` | Proxy configuration, read case-insensitively; `settings.json` takes precedence |
| `GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT` | Google Vertex project id |
| `GOOGLE_CLOUD_LOCATION` | Google Vertex region |
| `GOOGLE_CLOUD_API_KEY` | Google Vertex API key when not using ADC |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a Google service-account JSON file |
| `CLAUDE_CONFIG_DIR` | Windows only: where to find Claude Code credentials (default `~/.claude`) |

Provider and model can be set permanently in `settings.json`. See [Settings](settings.md).

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

- [Usage Guide](usage.md): interactive mode and slash commands
- [Installation](installation.md): setup and credential precedence
- [Settings](settings.md): persistent configuration
- [Sessions](sessions.md): session storage and branching
- [Extensions](extensions.md): building extensions that use the RPC UI surface
