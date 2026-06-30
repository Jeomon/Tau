# Tau Subagents

The extension launches focused child Tau agents with independent sessions,
tools, prompts, models, memory, and optional Git worktree isolation.

## Commands

| Command | Purpose |
|---|---|
| `/agents` or `/agents list` | List profiles and active agents |
| `/agents create [name]` | Create a project or global Markdown profile |
| `/agents view <id>` | Open the persisted conversation viewer |
| `/agents stop <id>` | Stop a running agent |
| `/agents result <id>` | Show a completed result |
| `/agents schedules` | List jobs for the current parent session |
| `/agents cancel <id>` | Cancel a scheduled job |

## Profile locations

Profiles are loaded in descending precedence:

1. `<project>/.tau/subagents/agents/<name>.md`
2. `~/.tau/subagents/agents/<name>.md`
3. Bundled `builtin_agents/<name>.md`

Example:

```markdown
---
display_name: Security reviewer
description: Reviews changes for exploitable security defects.
tools: read, grep, glob, ls
disallowed_tools: terminal
skills: security-review
model: openai/gpt-5
max_turns: 20
memory: project
run_in_background: true
inherit_context: false
isolated: false
isolation: none
enabled: true
---
Review only the requested change. Report concrete, reproducible findings.
```

`isolation: worktree` requires a Git repository with at least one commit. Changes
are committed to `tau-agent-<agent-id>` and the temporary checkout is removed.

## Persistent data

Run metadata and transcripts stay with the active project:

```text
.tau/subagents/
├── agents/
├── memory/<profile>/MEMORY.md
├── memory-local/<profile>/MEMORY.md
├── output/<agent-id>/
│   ├── record.json
│   └── session.jsonl
└── schedules/<parent-session-id>.json
```

Memory scopes:

- `project`: `.tau/subagents/memory/<name>/`
- `local`: `.tau/subagents/memory-local/<name>/`
- `user`: `~/.tau/subagents/memory/<name>/`

`MEMORY.md` is injected into subsequent runs up to 200 lines. Agents with write,
edit, or terminal access may maintain memory; read-only agents only consume it.

## Scheduling

The `Agent` tool accepts:

- Interval: `5m`, `1h`, `2d`
- Relative one-shot: `+10m`
- Absolute one-shot: ISO 8601 timestamp
- Cron: six fields (`second minute hour day month weekday`)

Scheduled jobs are scoped to the parent Tau session, restored on resume, forced
into background mode, and cannot inherit context or resume another agent.

## Extension API

The `subagents` service published with `tau.provide()` exposes:

- `subscribe(callback)` for `created`, `started`, `completed`, `failed`, and
  `steered` lifecycle events
- `rpc_ping()`
- `rpc_spawn(request)`
- `rpc_stop(agent_id)`

RPC methods return serializable `{success, data, error}` envelopes.
