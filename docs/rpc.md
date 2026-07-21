# RPC Mode

RPC mode runs Tau headlessly and speaks JSON Lines over stdin and stdout. It is the integration surface for IDE plugins, editor extensions, and custom front-ends that want the full agent loop without a terminal UI.

If you are writing Python, consider driving `tau.runtime.service.Runtime` in process instead — see [Python API](python-api.md). RPC mode exists for clients in other languages, or for any client that wants process isolation.

## Table of Contents

- [Starting RPC Mode](#starting-rpc-mode)
- [Transport and Framing](#transport-and-framing)
- [Lifecycle](#lifecycle)
- [Message Shapes](#message-shapes)
- [Commands](#commands)
  - [Prompting](#prompting)
  - [State and Messages](#state-and-messages)
  - [Model and Thinking](#model-and-thinking)
  - [Queue Modes](#queue-modes)
  - [Compaction and Retry](#compaction-and-retry)
  - [Terminal](#terminal)
  - [Session](#session)
  - [Commands Discovery](#commands-discovery)
  - [Extension UI Response](#extension-ui-response)
- [Events](#events)
- [Extension UI Protocol](#extension-ui-protocol)
- [Error Handling](#error-handling)
- [Known Gaps](#known-gaps)
- [Worked Example Session](#worked-example-session)
- [Python Client](#python-client)
- [Next Steps](#next-steps)

## Starting RPC Mode

```bash
tau --mode rpc                              # Default model and session handling
tau --mode rpc --ephemeral                  # Do not persist the session
tau --mode rpc --model anthropic/claude-sonnet-4-5
tau --mode rpc --cwd /path/to/project       # Set the working directory
tau --mode rpc --approve                    # Trust project-local files (no prompt is shown)
tau --mode rpc --session-dir /tmp/sessions  # Custom session storage
tau --mode rpc --name "ide-session"         # Session display name
```

Useful global flags:

| Flag | Short | Description |
|------|-------|-------------|
| `--mode rpc` | | Select RPC mode |
| `--cwd PATH` | `-c` | Working directory for the session |
| `--model` | | Model ID, or `provider/model` shorthand |
| `--provider` | | Provider override |
| `--effort` | | Thinking level for this run |
| `--ephemeral` | `-e` | Do not write the session to disk |
| `--session-dir PATH` | | Session storage directory |
| `--resume [ID]` | `-r` | Resume a session |
| `--name NAME` | | Session display name |
| `--tools NAMES` | | Comma-separated tool allowlist |
| `--system TEXT` | `-s` | Replace the generated system prompt |
| `--approve` / `--no-approve` | `-a` / `-na` | Force the project trust decision |

RPC mode never shows the interactive project-trust prompt. Under the default `"ask"` policy with no stored decision, the project is treated as untrusted and project settings, context files, and the git snapshot are skipped. Pass `--approve` when the client intends to load project configuration. See [Security](security.md#overriding-trust-for-one-run).

See [CLI Reference](cli-reference.md) for the complete flag list.

## Transport and Framing

| Property | Value |
|----------|-------|
| Direction | Client writes commands to stdin; Tau writes events and responses to stdout |
| Encoding | UTF-8; undecodable bytes are replaced rather than raising |
| Record delimiter | `\n`. A trailing `\r` is stripped, so `\r\n` input is accepted |
| Record content | Exactly one JSON object per line |
| Blank lines | Ignored |
| Flushing | Every outgoing line is flushed immediately |

Because framing is strictly newline-delimited, a client must split on `\n` only. Do not use a line reader that also breaks on Unicode separators such as `U+2028` and `U+2029` — those are legal inside JSON strings and will corrupt records.

**stdout belongs to the protocol.** On entry, RPC mode duplicates the real stdout for its own use and points file descriptor 1 at stderr. A `print` from a tool, an extension, or a subprocess therefore lands on stderr and cannot appear in the middle of a JSON line. Clients should read stderr separately (or discard it) — it carries diagnostics only, never protocol records.

**Backpressure.** Outgoing lines go through an asyncio pipe writer, and the event forwarder waits for the pipe to drain between events. A client that reads slowly slows the event stream instead of stalling the agent's event loop inside a blocking write. A client that stops reading entirely will eventually stop the agent's progress — read continuously, even if you discard.

Values that are not JSON-native are coerced rather than dropped: enums become their value, `bytes` become base64, sets and tuples become arrays, paths become strings, and anything else becomes its `str()`. A single odd field can never break the stream.

Commands are dispatched concurrently. Each parsed line is handed to a fire-and-forget task, so responses are **not** guaranteed to arrive in the order the commands were sent, and events for an in-flight prompt interleave freely with responses to later commands. Always correlate with the `id` field.

## Lifecycle

1. The client spawns `tau --mode rpc`. Tau boots the full runtime — settings, model, session manager, resources, extensions.
2. Tau subscribes to the agent event hooks and writes a single `ready` line.
3. The client writes commands as JSON lines. Tau writes a `response` line for each (except `extension_ui_response`) plus a stream of events.
4. Shutdown is triggered by EOF on stdin, `SIGTERM`, or `SIGHUP`. On a signal, Tau aborts the running agent first, then unsubscribes and exits.

The `ready` line is the handshake. It is the first line written and carries the session identity:

```json
{"type": "ready", "sessionId": "0f9c1c4a", "cwd": "/home/user/project"}
```

Both fields are `null` when there is no session manager. There is no version or capability negotiation — the client should treat `ready` as "the runtime is up, start sending commands".

## Message Shapes

Three kinds of line come out of Tau.

| Line type | Discriminator | Carries `id` |
|-----------|---------------|--------------|
| Handshake | `"type": "ready"` | No |
| Command response | `"type": "response"` | Only when the command supplied one |
| Event | `"type": "<event name>"` | No |
| Extension UI request | `"type": "extension_ui_request"` | Yes, its own request id |

Every command accepts an optional `id`. When present it is echoed on the response; when absent the response has no `id` field at all.

```json
{"type": "response", "command": "prompt", "success": true}
{"type": "response", "command": "get_state", "id": "req-7", "success": true, "data": {}}
{"type": "response", "command": "set_model", "id": "req-8", "success": false, "error": "…"}
```

| Field | Type | Present |
|-------|------|---------|
| `type` | `"response"` | Always |
| `command` | string | Always — the `type` of the command being answered |
| `id` | string | Only if the command carried one |
| `success` | bool | Always |
| `data` | any | Only when the handler produced a payload |
| `error` | string | Only when `success` is `false` |

## Commands

Every command type declared in `tau/modes/rpc/types.py`, in full.

| Command | Fields | Response `data` |
|---------|--------|-----------------|
| `prompt` | `message`, `streamingBehavior?` | — |
| `steer` | `message` | — |
| `follow_up` | `message` | — |
| `abort` | — | — |
| `new_session` | `parentSession?` | `{cancelled}` |
| `get_state` | — | session state object |
| `set_model` | `modelId`, `provider?` | `{id, provider}` or `null` |
| `cycle_model` | — | `{model}` or `null` |
| `get_available_models` | — | `{models}` |
| `set_thinking_level` | `level` | — |
| `cycle_thinking_level` | — | `{level}` or `null` |
| `set_steering_mode` | `mode` | — |
| `set_follow_up_mode` | `mode` | — |
| `compact` | `customInstructions?` | compaction fields or `null` |
| `set_auto_compaction` | `enabled` | — |
| `set_auto_retry` | `enabled` | — |
| `abort_retry` | — | — |
| `terminal` | `command`, `excludeFromContext?` | — |
| `abort_terminal` | — | — |
| `get_session_stats` | — | stats object |
| `export_html` | `outputPath?` | always fails |
| `switch_session` | `sessionPath` | `{cancelled}` |
| `fork` | `entryId`, `position?` | `{text, cancelled}` |
| `clone` | — | `{cancelled}` |
| `get_fork_messages` | — | `{messages}` |
| `get_last_assistant_text` | — | `{text}` |
| `set_session_name` | `name` | — |
| `get_messages` | — | `{messages}` |
| `get_commands` | — | `{commands}` |
| `extension_ui_response` | `value?`, `confirmed?`, `cancelled?` | no response line |

### Prompting

#### prompt

Send a user prompt. `message` is required; an empty or missing message is an error.

```json
{"id": "req-1", "type": "prompt", "message": "List the Python files in this directory."}
```

```json
{"id": "req-1", "type": "response", "command": "prompt", "success": true}
```

The response means **accepted and started**, not finished. It is written as soon as the turn is under way, so a client can act on the ack while events for the run stream out behind it. Wait for `settled` to know a turn is complete. A failure that happens before the turn starts (no model, no session) is reported on the response instead; anything that fails after it starts arrives as an `agent_error` event. The prompt text is passed through the `input` hook first, so an extension may transform or suppress it.

The optional `streamingBehavior` field takes `"steer"` or `"followUp"` and only applies when the agent is mid-run. Sending `prompt` while a turn is in flight **without** it is an error:

```json
{"id": "req-2", "type": "response", "command": "prompt", "success": false, "error": "Agent is streaming; specify streamingBehavior: 'steer' or 'followUp'"}
```

`prompt` also accepts `attachments` — see [Attachments](#attachments) below.

#### steer

Queue a steering message on the running engine. Requires an active agent.

```json
{"type": "steer", "message": "Skip the tests directory."}
```

```json
{"type": "response", "command": "steer", "success": true}
```

Fails with `"No active agent"` if no agent exists.

#### follow_up

Queue a message to be delivered after the current run finishes.

```json
{"type": "follow_up", "message": "Then write a summary."}
```

```json
{"type": "response", "command": "follow_up", "success": true}
```

#### Attachments

`prompt`, `steer`, and `follow_up` all accept an `attachments` array alongside (or instead of) `message` — a request with attachments and no text is valid.

```json
{"id": "req-3", "type": "prompt", "message": "What is in this screenshot?",
 "attachments": [{"kind": "image", "path": "/tmp/shot.png"}]}
```

| Field | Values |
|-------|--------|
| `kind` | `image`, `audio`, `video`, `file` — required |
| `data` | base64-encoded bytes |
| `path` | server-side path, read into bytes by Tau |
| `url` | remote URL — **images only** |
| `mimeType`, `name` | optional metadata |

Exactly one of `data`, `path`, or `url` must be present per attachment. Violations fail the whole command with `"invalid attachment: …"` and nothing is sent to the model.

#### abort

Cancel the current agent operation. Always succeeds, even when nothing is running.

```json
{"type": "abort"}
```

```json
{"type": "response", "command": "abort", "success": true}
```

#### new_session

Start a fresh session. The `parentSession` field is accepted by the schema but not used by the handler.

```json
{"type": "new_session"}
```

```json
{"type": "response", "command": "new_session", "success": true, "data": {"cancelled": false}}
```

If starting the session raises, the error is logged and the response reports `{"cancelled": true}` with `success: true` — not a failure response.

### State and Messages

#### get_state

```json
{"id": "s1", "type": "get_state"}
```

```json
{
  "id": "s1",
  "type": "response",
  "command": "get_state",
  "success": true,
  "data": {
    "model": {"id": "claude-sonnet-4-5", "provider": "anthropic"},
    "thinkingLevel": "medium",
    "isStreaming": false,
    "isCompacting": false,
    "sessionFile": "/home/user/.tau/sessions/20260720_0f9c1c4a.jsonl",
    "sessionId": "0f9c1c4a",
    "autoCompactionEnabled": true,
    "messageCount": 4,
    "pendingMessageCount": 0
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `model` | object \| null | Only `id` and `provider`, not the full model descriptor |
| `thinkingLevel` | string \| null | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `isStreaming` | bool | Always `false` — see [Known Gaps](#known-gaps) |
| `isCompacting` | bool | Hard-coded `false` |
| `sessionFile` | string \| null | Empty string when the session is ephemeral |
| `sessionId` | string \| null | |
| `autoCompactionEnabled` | bool | From the agent's compaction config |
| `messageCount` | int | Message entries on the active branch |
| `pendingMessageCount` | int | Hard-coded `0` |

The `RpcSessionState` type additionally declares `steeringMode`, `followUpMode`, and `sessionName`; the handler does not emit them.

#### get_messages

Returns the active branch flattened to role and text.

```json
{"type": "get_messages"}
```

```json
{
  "type": "response",
  "command": "get_messages",
  "success": true,
  "data": {
    "messages": [
      {"role": "user", "text": "List the Python files in this directory."},
      {"role": "assistant", "text": "There are 3 Python files: a.py, b.py, c.py."}
    ]
  }
}
```

Text is the concatenation of every content block exposing a string `content` field, which includes text and thinking blocks. Tool calls, tool results, images, and usage are not represented. Roles come from the message `role` enum: `system`, `user`, `assistant`, `tool`, `custom`, `skill_invocation`, `template_invocation`, `terminal_execution`, `compaction_summary`, `branch_summary`.

#### get_session_stats

```json
{"type": "get_session_stats"}
```

```json
{
  "type": "response",
  "command": "get_session_stats",
  "success": true,
  "data": {
    "sessionFile": "/home/user/.tau/sessions/20260720_0f9c1c4a.jsonl",
    "sessionId": "0f9c1c4a",
    "userMessages": 2,
    "assistantMessages": 2,
    "totalMessages": 4,
    "cwd": "/home/user/project",
    "contextUsage": null
  }
}
```

`totalMessages` counts only user plus assistant messages. When there is no session manager, the payload degrades to `{"sessionId": null, "totalMessages": 0, "cwd": null}`. `contextUsage` is always `null` in the current implementation — see [Known Gaps](#known-gaps).

#### get_last_assistant_text

```json
{"type": "get_last_assistant_text"}
```

```json
{"type": "response", "command": "get_last_assistant_text", "success": true, "data": {"text": "There are 3 Python files: a.py, b.py, c.py."}}
```

Returns `{"text": null}` when there is no assistant message.

### Model and Thinking

#### set_model

`modelId` is required; `model_id` is accepted as an alias. `provider` is optional.

```json
{"type": "set_model", "modelId": "claude-sonnet-4-5", "provider": "anthropic"}
```

```json
{"type": "response", "command": "set_model", "success": true, "data": {"id": "claude-sonnet-4-5", "provider": "anthropic"}}
```

`data` is `null` if there is no agent to read the new model back from.

The swap returns a boolean internally and the handler ignores it, so an unknown model id or a provider with missing credentials still answers `success: true` — with `data` reporting the model that is *still* active. Compare the returned `id` and `provider` against what you asked for to detect a failed switch.

#### cycle_model

Advances to the next model in the available list, wrapping around.

```json
{"type": "cycle_model"}
```

```json
{"type": "response", "command": "cycle_model", "success": true, "data": {"model": {"id": "gpt-5", "provider": "openai"}}}
```

`data` is `null` when the current model is not in the available list, when there is no agent, or when anything in the lookup fails — the handler swallows errors and still reports `success: true`.

#### get_available_models

Lists every text model whose provider has usable authentication.

```json
{"type": "get_available_models"}
```

```json
{
  "type": "response",
  "command": "get_available_models",
  "success": true,
  "data": {
    "models": [
      {"id": "claude-sonnet-4-5", "provider": "anthropic", "name": "Claude Sonnet 4.5", "contextWindow": null}
    ]
  }
}
```

`contextWindow` is always `null`: the handler reads a `context_length` attribute that the model type does not define. The real field is `context_window`.

#### set_thinking_level

```json
{"type": "set_thinking_level", "level": "high"}
```

```json
{"type": "response", "command": "set_thinking_level", "success": true}
```

Valid levels are `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`. An invalid level produces a failure response. A valid level currently has no effect — see [Known Gaps](#known-gaps).

#### cycle_thinking_level

```json
{"type": "cycle_thinking_level"}
```

```json
{"type": "response", "command": "cycle_thinking_level", "success": true, "data": {"level": "high"}}
```

`data` is `null` when the level could not be determined.

### Queue Modes

#### set_steering_mode / set_follow_up_mode

```json
{"type": "set_steering_mode", "mode": "one-at-a-time"}
{"type": "set_follow_up_mode", "mode": "all"}
```

```json
{"type": "response", "command": "set_steering_mode", "success": true}
```

`mode` is `"all"` or `"one-at-a-time"`; hyphens are converted to underscores to match the internal enum, so `"one_at_a_time"` is also accepted. Both commands currently no-op — see [Known Gaps](#known-gaps).

### Compaction and Retry

#### compact

```json
{"type": "compact", "customInstructions": "Keep the file paths."}
```

```json
{"type": "response", "command": "compact", "success": true, "data": {"summary": "", "firstKeptEntryId": null, "tokensBefore": null}}
```

Compaction itself runs. The `data` fields are read from the return value of the agent's `compact()`, which is a boolean, so `summary` is always `""` and the other two are always `null`. Use the `compaction_end` event for real numbers.

#### set_auto_compaction

```json
{"type": "set_auto_compaction", "enabled": false}
```

```json
{"type": "response", "command": "set_auto_compaction", "success": true}
```

Flips the agent's compaction config for the running process.

#### set_auto_retry

```json
{"type": "set_auto_retry", "enabled": true}
```

```json
{"type": "response", "command": "set_auto_retry", "success": true}
```

Writes through to the settings manager's retry toggle.

#### abort_retry

```json
{"type": "abort_retry"}
```

```json
{"type": "response", "command": "abort_retry", "success": true}
```

Looks for an `abort_retry` method on the agent; there is none, so this is a no-op that always succeeds.

### Terminal

#### terminal

Runs a shell command through the runtime's terminal path: extensions can intercept it, output is streamed, and the result is persisted to the session as a terminal-execution message so it becomes part of the model's context.

```json
{"type": "terminal", "command": "git status --short"}
```

```json
{"type": "response", "command": "terminal", "success": true}
```

`command` is required. Set `excludeFromContext` (alias `exclude_from_context`) to `true` to run it without adding it to the model's context.

The response carries no `data` — output arrives as events instead. `terminal_execution` fires at start (`streaming: true`) and at completion (`streaming: false`), with `terminal_output` events carrying each chunk in between.

#### abort_terminal

```json
{"type": "abort_terminal"}
```

Looks for an `abort_terminal` method on the agent; there is none, so this is a no-op that always succeeds.

### Session

#### switch_session

```json
{"type": "switch_session", "sessionPath": "/home/user/.tau/sessions/20260719_ab12cd34.jsonl"}
```

```json
{"type": "response", "command": "switch_session", "success": true, "data": {"cancelled": false}}
```

`sessionPath` is required; `path` is accepted as an alias. If resuming raises — a missing or unreadable file — the response is a failure with the exception text. `cancelled` is always `false`.

#### fork

Branch the session at an entry. `entryId` is required (`entry_id` is accepted); `position` is `"at"` (default) or `"before"`.

```json
{"type": "fork", "entryId": "e7c1", "position": "before"}
```

```json
{"type": "response", "command": "fork", "success": true, "data": {"text": "List the Python files in this directory.", "cancelled": false}}
```

`text` is the concatenated text of the user message at that entry, read before the fork, and is `""` when the entry is not a user message. An unknown entry id produces a failure response. `cancelled` is always `false`; an extension cancelling the fork is not distinguished.

#### clone

Forks at the current leaf.

```json
{"type": "clone"}
```

```json
{"type": "response", "command": "clone", "success": true, "data": {"cancelled": false}}
```

Fails with `"No active session"` when there is no session manager.

#### get_fork_messages

Every user message on the active branch, with the entry id to pass back to `fork`.

```json
{"type": "get_fork_messages"}
```

```json
{
  "type": "response",
  "command": "get_fork_messages",
  "success": true,
  "data": {
    "messages": [
      {"entryId": "e7c1", "text": "List the Python files in this directory."},
      {"entryId": "f2a9", "text": "Now count the lines."}
    ]
  }
}
```

#### set_session_name

```json
{"type": "set_session_name", "name": "refactor-auth"}
```

```json
{"type": "response", "command": "set_session_name", "success": true}
```

Looks for a `set_name` method on the session manager; there is none, so this is a no-op. Set the name at launch with `--name` instead.

#### export_html

Declared in the protocol types but not implemented.

```json
{"type": "export_html"}
```

```json
{"type": "response", "command": "export_html", "success": false, "error": "export_html is not supported in this build"}
```

### Commands Discovery

#### get_commands

Extension commands, prompt templates, and skills, each invocable by sending `prompt` with a leading `/`.

```json
{"type": "get_commands"}
```

```json
{
  "type": "response",
  "command": "get_commands",
  "success": true,
  "data": {
    "commands": [
      {"name": "reload", "description": "Reload extensions", "source": "extension"},
      {"name": "review", "description": "Review the working diff", "source": "prompt"},
      {"name": "skill:code-review", "description": "Review code for correctness, security, and maintainability", "source": "skill"}
    ]
  }
}
```

| Field | Description |
|-------|-------------|
| `name` | Invoke as `/name`. Skills are prefixed with `skill:` |
| `description` | Human-readable description; `""` for skills without one |
| `source` | `"extension"`, `"prompt"`, or `"skill"` |

There is no `path` or `location` field. If the prompt or skill registry raises, that group is omitted and the rest is still returned.

### Extension UI Response

The one command that produces no response line. It resolves a pending `extension_ui_request` future by id.

```json
{"type": "extension_ui_response", "id": "ui_1", "value": "Allow"}
{"type": "extension_ui_response", "id": "ui_2", "confirmed": true}
{"type": "extension_ui_response", "id": "ui_3", "cancelled": true}
```

Resolution rules, in order: `cancelled` truthy resolves to `null`; otherwise a present `confirmed` key resolves to `{"confirmed": <bool>}`; otherwise the `value` field is used. An id with no pending request is silently ignored.

## Events

Tau subscribes to the hook events below and writes each one as a JSON line. Events never carry an `id`.

Serialization is mechanical: the event dataclass is converted to a dict, so field names are the Python `snake_case` names, not `camelCase`. A non-dataclass event keeps its attributes too (private `_`-prefixed ones are stripped) and uses its `type` attribute, falling back to the class name. Response and `extension_error` payloads are hand-built and stay `camelCase`.

| Event | Payload fields |
|-------|----------------|
| `agent_start` | — |
| `agent_end` | `messages`, `reason` |
| `agent_error` | `error` |
| `turn_start` | `turn_index`, `timestamp` |
| `turn_end` | `turn_index`, `message`, `tool_results` |
| `message_start` | `message` |
| `message_update` | `message` |
| `message_end` | `message` |
| `message_rollback` | `count` |
| `tool_execution_start` | `tool_call` |
| `tool_execution_update` | `partial_tool_result` |
| `tool_execution_end` | `tool_result` |
| `tool_execution_failure` | `tool_name`, … |
| `compaction_start` | `manual`, `reason`, `will_retry` |
| `compaction_end` | `manual`, `tokens_before`, `summary_length`, `from_extension`, `reason`, `will_retry` |
| `compaction_cancelled` | — |
| `compaction_failure` | — |
| `terminal_execution` | `message`, `streaming` |
| `terminal_output` | `message` |
| `queue_update` | `queue`, `message`, `messages` |
| `settled` | — |
| `extension_error` | `extensionPath`, `event`, `error`, `stack` |

`message_rollback` retracts the last `count` committed messages — an interrupted tool turn persists an assistant tool-call message and its result before the abort lands, and both must be dropped. A client that mirrors the transcript and ignores this event drifts out of sync with the session file.

`extension_error` reports an extension that failed to load or whose handler raised. Extensions that failed at startup are reported once, right after `ready`. The agent keeps running either way.

Enumerated values:

| Field | Values |
|-------|--------|
| `agent_end.reason` | `completed`, `aborted`, `error` |
| `compaction_*.reason` | `manual`, `threshold`, `overflow` |
| `queue_update.queue` | `steering`, `followup` |
| `message.stop_reason` | `stop`, `length`, `tool_calls`, `content_filter`, `abort`, `error` |

### message_update

`message_update` carries the whole partial message on each tick, not a delta. Clients redraw from `message.contents` rather than appending. There is no separate delta-event field.

```json
{"type": "message_update", "message": {"contents": [{"type": "text", "content": "There are 3 Py"}], "id": "…", "timestamp": 1784547215.53, "role": "assistant", "usage": {}, "stop_reason": "stop", "error": "", "error_kind": "unknown"}}
```

### message_end

```json
{
  "type": "message_end",
  "message": {
    "contents": [{"type": "text", "content": "There are 3 Python files: a.py, b.py, c.py."}],
    "id": "1ff30f22-4ae1-4367-bf20-74a08964d24b",
    "timestamp": 1784547215.538317,
    "role": "assistant",
    "usage": {
      "input_tokens": 1204,
      "output_tokens": 38,
      "cache_read_tokens": 0,
      "cache_write_tokens": 0,
      "cache_write_1h_tokens": 0,
      "input_tokens_include_cache_read": false,
      "cost": {"input": 0.0036, "output": 0.00057, "cache_read": 0.0, "cache_write": 0.0, "total": 0.00417}
    },
    "stop_reason": "stop",
    "error": "",
    "error_kind": "unknown"
  }
}
```

### agent_end

```json
{"type": "agent_end", "messages": [{"role": "assistant", "contents": [], "…": "…"}], "reason": "completed"}
```

### Tool events

`tool_execution_start` wraps a tool-call content block:

```json
{"type": "tool_execution_start", "tool_call": {"type": "tool_call", "id": "call_1", "name": "read", "kind": "read", "args": {"path": "a.py"}, "metadata": {}}}
```

`tool_execution_end` wraps a tool-result content block:

```json
{
  "type": "tool_execution_end",
  "tool_result": {
    "type": "tool_result",
    "id": "call_1",
    "content": "…file contents…",
    "is_error": false,
    "metadata": {},
    "terminate": false,
    "terminate_message": null,
    "tool_name": "read",
    "image": null,
    "audio": null,
    "video": null
  }
}
```

Correlate start, update, and end by the `id` field, which is the tool-call id.

### compaction_end

```json
{"type": "compaction_end", "manual": true, "tokens_before": 152000, "summary_length": 3184, "from_extension": false, "reason": "manual", "will_retry": false}
```

### queue_update

```json
{"type": "queue_update", "queue": "steering", "message": null, "messages": []}
```

### settled

Emitted when the agent finishes a prompt with nothing queued. This is the signal a client should wait on before considering a turn complete — `agent_end` can be followed by retries or queued continuations.

```json
{"type": "settled"}
```

## Extension UI Protocol

RPC mode defines a request/response sub-protocol so extensions that need user interaction can reach the client. Requests use `type: "extension_ui_request"` with a unique `id` (`ui_1`, `ui_2`, …) and a `method`.

Dialog methods block until the client replies with a matching `extension_ui_response`:

| Method | Request fields | Expected reply |
|--------|----------------|----------------|
| `select` | `title`, `options` | `value` (chosen option) or `cancelled` |
| `confirm` | `title`, `message` | `confirmed` or `cancelled` |
| `input` | `title`, `placeholder` | `value` or `cancelled` |
| `editor` | `title`, `prefill` | `value` or `cancelled` |

Fire-and-forget methods expect no reply:

| Method | Request fields |
|--------|----------------|
| `notify` | `message`, `notifyType` (`info`, `warning`, `error`) |
| `setStatus` | `statusKey`, `statusText` (`null` clears) |
| `setWidget` | `widgetKey`, `widgetLines` (`null` clears), `widgetPlacement` (`aboveEditor`, `belowEditor`) |
| `setTitle` | `title` |
| `set_editor_text` | `text` |

```json
{"type": "extension_ui_request", "id": "ui_1", "method": "select", "title": "Allow this command?", "options": ["Allow", "Block"]}
{"type": "extension_ui_response", "id": "ui_1", "value": "Allow"}
```

`timeout`, when present on a request, is the number of **milliseconds** Tau will wait before giving up on that dialog. A timeout resolves the same way a cancel does (`null`, or `false` for `confirm`). Dialogs sent without a `timeout` wait indefinitely — but not past the end of the session: when the client disconnects or the process shuts down, every pending dialog is resolved as cancelled so nothing blocks the exit.

> **Extension reach.** RPC mode installs `RpcExtensionUIContext` as the runtime's dialog bridge, so `ctx.select(...)` and `ctx.confirm(...)` from an extension emit real `extension_ui_request` lines and `ctx.has_ui` is `True`. The rich TUI surface (`ctx.ui` — widgets, footers, themes) stays `None` in RPC mode; the fire-and-forget methods above are part of the protocol but are not yet reachable from `ctx`. Guard extension code with `ctx.has_ui` for dialogs and with `ctx.ui is not None` for TUI customization.

## Error Handling

A failed command returns a response with `success: false` and an `error` string.

```json
{"type": "response", "command": "prompt", "id": "req-1", "success": false, "error": "'message' is required"}
```

| Situation | Response |
|-----------|----------|
| Malformed JSON line | `{"type": "response", "command": "parse", "success": false, "error": "Failed to parse command: …"}` |
| Unknown `type` | `error: "Unknown command type: '<type>'"` |
| Missing required field | e.g. `"'message' is required"`, `"'modelId' is required"`, `"'command' is required"`, `"'entryId' is required"`, `"'sessionPath' is required"` |
| No agent for `steer`/`follow_up` | `"No active agent"` |
| No session for `clone` | `"No active session"` |
| Unhandled exception in a handler | `error` is `str(exception)` |

A parse error is reported with `command: "parse"` and never has an `id`, since the id could not be read. Parse errors do not terminate the loop — the next line is processed normally.

Note the asymmetry in a few handlers: `new_session` reports internal failure as `success: true` with `data.cancelled: true`, and `cycle_model` swallows lookup failures and returns `success: true` with `data: null`.

## Known Gaps

Verified against the implementation. These commands accept input and answer `success: true`, but do not change state.

| Command / field | Behaviour | Cause |
|-----------------|-----------|-------|
| `set_thinking_level` / `cycle_thinking_level` | No effect on the model | The handler calls `set_thinking_level` on the LLM object, which does not define it |
| `set_steering_mode` / `set_follow_up_mode` | No effect | The queues live on `engine.state`, not on `engine` where the handler looks |
| `set_session_name` | No effect | The session manager has no `set_name` method |
| `abort_retry`, `abort_terminal` | No effect | The agent has no such methods |
| `compact` response `data` | `summary` `""`, `firstKeptEntryId` and `tokensBefore` `null` | The agent's `compact()` returns a bool, not a result object |
| `get_session_stats.contextUsage` | Always `null` | The handler reads `context_usage` off the engine; the real accessor is the agent's `get_context_usage()` |
| `get_available_models[].contextWindow` | Always `null` | The handler reads `context_length`; the field is `context_window` |
| `set_model` | Reports `success: true` even when the switch failed | The handler ignores the boolean result and reads the (unchanged) active model back |
| `export_html` | Always fails | Not implemented |

Use `abort` (which does work, via the agent's `abort()`), the `compaction_end` event, and launch flags such as `--effort` and `--name` as the reliable alternatives.

## Worked Example Session

A complete exchange. Lines marked `→` are written by the client to stdin; `←` are written by Tau to stdout. JSON is shown one object per line as it appears on the wire.

```bash
tau --mode rpc --ephemeral --cwd /home/user/project
```

```text
← {"type":"ready","sessionId":"0f9c1c4a","cwd":"/home/user/project"}

→ {"id":"1","type":"get_state"}
← {"type":"response","command":"get_state","id":"1","success":true,"data":{"model":{"id":"claude-sonnet-4-5","provider":"anthropic"},"thinkingLevel":"medium","isStreaming":false,"isCompacting":false,"sessionFile":"","sessionId":"0f9c1c4a","autoCompactionEnabled":true,"messageCount":0,"pendingMessageCount":0}}

→ {"id":"2","type":"prompt","message":"How many lines are in README.md?"}
← {"type":"agent_start"}
← {"type":"turn_start","turn_index":0,"timestamp":1784547215.11}
← {"type":"message_start","message":{"contents":[],"id":"a1","timestamp":1784547215.12,"role":"assistant","usage":{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_write_tokens":0,"cache_write_1h_tokens":0,"input_tokens_include_cache_read":false,"cost":{"input":0.0,"output":0.0,"cache_read":0.0,"cache_write":0.0,"total":0.0}},"stop_reason":"stop","error":"","error_kind":"unknown"}}
← {"type":"message_update","message":{"contents":[{"type":"text","content":"Let me"}],"id":"a1","…":"…"}}
← {"type":"message_update","message":{"contents":[{"type":"text","content":"Let me check."},{"type":"tool_call","id":"call_1","name":"read","kind":"read","args":{},"metadata":{}}],"id":"a1","…":"…"}}
← {"type":"message_end","message":{"contents":[{"type":"text","content":"Let me check."},{"type":"tool_call","id":"call_1","name":"read","kind":"read","args":{"path":"README.md"},"metadata":{}}],"id":"a1","stop_reason":"tool_calls","…":"…"}}
← {"type":"tool_execution_start","tool_call":{"type":"tool_call","id":"call_1","name":"read","kind":"read","args":{"path":"README.md"},"metadata":{}}}
← {"type":"tool_execution_end","tool_result":{"type":"tool_result","id":"call_1","content":"# Project\n…","is_error":false,"metadata":{},"terminate":false,"terminate_message":null,"tool_name":"read","image":null,"audio":null,"video":null}}
← {"type":"turn_end","turn_index":0,"message":{"…":"…"},"tool_results":[{"…":"…"}]}
← {"type":"turn_start","turn_index":1,"timestamp":1784547217.02}
← {"type":"message_end","message":{"contents":[{"type":"text","content":"README.md has 42 lines."}],"id":"a2","stop_reason":"stop","…":"…"}}
← {"type":"turn_end","turn_index":1,"message":{"…":"…"},"tool_results":[]}
← {"type":"agent_end","messages":[{"…":"…"}],"reason":"completed"}
← {"type":"settled"}
← {"type":"response","command":"prompt","id":"2","success":true}

→ {"id":"3","type":"get_last_assistant_text"}
← {"type":"response","command":"get_last_assistant_text","id":"3","success":true,"data":{"text":"README.md has 42 lines."}}

→ {"id":"4","type":"terminal","command":"git status --short"}
← {"type":"response","command":"terminal","id":"4","success":true}

→ {"id":"5","type":"prompt","message":"Now summarise the git status output."}
← {"type":"agent_start"}
← {"…":"… more events …"}
← {"type":"settled"}
← {"type":"response","command":"prompt","id":"5","success":true}

→ {"id":"6","type":"set_model"}
← {"type":"response","command":"set_model","id":"6","success":false,"error":"'modelId' is required"}

→ {"id":"7","type":"nonsense"}
← {"type":"response","command":"nonsense","id":"7","success":false,"error":"Unknown command type: 'nonsense'"}

→ not json
← {"type":"response","command":"parse","success":false,"error":"Failed to parse command: Expecting value: line 1 column 1 (char 0)"}
```

Closing stdin ends the session; Tau unsubscribes its hooks and exits.

## Python Client

A complete client that starts Tau, sends one prompt, prints assistant text as it streams, and exits when the run settles.

```python
import json
import subprocess
import threading


class TauRpc:
    def __init__(self, *args: str) -> None:
        self.proc = subprocess.Popen(
            ["tau", "--mode", "rpc", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 0
        self._lock = threading.Lock()

    def send(self, **command) -> str:
        with self._lock:
            self._next_id += 1
            command["id"] = str(self._next_id)
            self.proc.stdin.write(json.dumps(command) + "\n")
            self.proc.stdin.flush()
        return command["id"]

    def lines(self):
        for raw in self.proc.stdout:
            line = raw.rstrip("\n").rstrip("\r")
            if line:
                yield json.loads(line)

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def text_of(message: dict) -> str:
    return "".join(
        block.get("content", "")
        for block in message.get("contents", [])
        if block.get("type") == "text"
    )


def main() -> None:
    client = TauRpc("--ephemeral")
    stream = client.lines()

    ready = next(stream)
    assert ready["type"] == "ready", ready
    print(f"session {ready['sessionId']} in {ready['cwd']}")

    client.send(type="prompt", message="How many lines are in README.md?")

    shown = ""
    for msg in stream:
        kind = msg.get("type")

        if kind == "message_update":
            current = text_of(msg["message"])
            if current.startswith(shown):
                print(current[len(shown):], end="", flush=True)
                shown = current

        elif kind == "tool_execution_start":
            call = msg["tool_call"]
            print(f"\n[{call['name']}] {call['args']}", flush=True)

        elif kind == "message_end":
            shown = ""
            print(flush=True)

        elif kind == "agent_error":
            print(f"\nerror: {msg['error']}")

        elif kind == "settled":
            break

    client.send(type="get_session_stats")
    for msg in stream:
        if msg.get("type") == "response" and msg.get("command") == "get_session_stats":
            print(msg["data"])
            break

    client.close()


if __name__ == "__main__":
    main()
```

Two details this client demonstrates and any real client needs:

- `message_update` carries the full partial message, so it diffs against what it has already printed instead of appending a delta.
- It waits for `settled`, not `agent_end`, before treating the turn as finished.

## Next Steps

- [CLI Reference](cli-reference.md) — every flag accepted alongside `--mode rpc`
- [Python API](python-api.md) — driving the runtime in process instead of over a pipe
- [Extensions](extensions.md) — hooks, commands, and the UI context RPC clients interact with
- [Sessions](sessions.md) — the session file format behind `fork`, `clone`, and `switch_session`
- [Security](security.md) — trust resolution in non-interactive modes
