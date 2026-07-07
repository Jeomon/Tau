# Subagent Example

Delegate tasks to specialized subagents with isolated context windows.

Ported from the [pi coding agent's reference `subagent` extension](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent):
instead of running in-process, each delegated task spawns a separate `tau`
process (`--mode json --ephemeral`), so it gets its own context window rather
than eating into the parent conversation's.

This extension also ships as a builtin (`tau/builtins/extensions/subagent`),
enabled by default — its sample agents (scout/planner/reviewer/worker) work
out of the box there with no manual symlinking. This copy is the standalone
reference version showing the manual install path below.

## Structure

```
subagent/
├── README.md            # This file
├── __init__.py           # The extension (entry point, register(tau))
├── agents.py              # Agent discovery (markdown + frontmatter)
├── subagent_schema.py     # Pydantic tool parameters
├── subagent_tool.py       # The tool: subprocess spawn + NDJSON parsing
└── agents/                # Sample agent definitions
    ├── scout.md            # Fast recon, read-only
    ├── planner.md           # Implementation plans, read-only
    ├── reviewer.md          # Code review, read-only + terminal
    └── worker.md            # General-purpose (full tool access)
```

## Installation

From the repository root, symlink the files:

```bash
mkdir -p ~/.tau/extensions/subagent
ln -sf "$(pwd)/examples/extensions/subagent/__init__.py" ~/.tau/extensions/subagent/__init__.py
ln -sf "$(pwd)/examples/extensions/subagent/agents.py" ~/.tau/extensions/subagent/agents.py
ln -sf "$(pwd)/examples/extensions/subagent/subagent_schema.py" ~/.tau/extensions/subagent/subagent_schema.py
ln -sf "$(pwd)/examples/extensions/subagent/subagent_tool.py" ~/.tau/extensions/subagent/subagent_tool.py

# Symlink sample agents
mkdir -p ~/.tau/agents
for f in examples/extensions/subagent/agents/*.md; do
  ln -sf "$(pwd)/$f" ~/.tau/agents/$(basename "$f")
done
```

Then register the extension in `~/.tau/settings.json`:

```json
{
  "extensions": {
    "list": [
      { "path": "~/.tau/extensions/subagent" }
    ]
  }
}
```

## Security model

This tool spawns a separate `tau` subprocess with a delegated system prompt
and tool allowlist.

**Project-local agents** (`.tau/agents/*.md`) are repo-controlled prompts that
can instruct the model to read files, run shell commands, etc.

**Default behavior:** only loads **user-level agents** from `~/.tau/agents`.

To enable project-local agents, pass `agent_scope: "both"` (or `"project"`).
Only do this for repositories you trust. When an interactive TUI session is
active, the tool asks for confirmation before running project-local agents
(disable with `confirm_project_agents: false`).

## Usage

### Single agent
```
Use scout to find all authentication code
```
→ `{"spawn": [{"agent": "scout", "task": "find all authentication code"}]}`

### Concurrent execution
```
Run 2 scouts in parallel: one to find models, one to find providers
```
→ `{"spawn": [{"agent": "scout", "task": "find models"}, {"agent": "scout", "task": "find providers"}]}`

### Chained workflow
```
Use a chain: first have scout find the read tool, then have planner suggest improvements
```
→ `{"chain": [{"agent": "scout", "task": "find the read tool"}, {"agent": "planner", "task": "suggest improvements to {previous}"}]}`

## Tool modes

There are two modes — a single task is just a one-item list in either:

| Mode | Parameters | Description |
|------|-----------|-------------|
| `spawn` | `{spawn: [{agent, task, cwd?}, ...]}` | Runs concurrently (max 8, 4 at a time) |
| `chain` | `{chain: [{agent, task, cwd?}, ...]}` | Runs sequentially, `{previous}` placeholder for the prior step's output |

Exactly one of `spawn` or `chain` must be provided per call.

## Background runs (`async`)

Add `async: true` to `spawn`/`chain` to return immediately instead of
blocking on completion:

```json
{ "spawn": [{ "agent": "worker", "task": "long-running refactor" }], "async": true }
```

This returns one run id per spawned task (or one run id for the whole
chain), which you then check on via `action`:

| Action | Parameters | Description |
|--------|-----------|-------------|
| `status` | `{action: "status", run_id?}` | Report on one run, or list every tracked run if `run_id` is omitted |
| `interrupt` | `{action: "interrupt", run_id}` | Stop a running background run, keeping its partial output |
| `resume` | `{action: "resume", run_id, message}` | Continue a finished or interrupted run with a new message (interrupting it first if it's still running) |

Foreground (non-async) runs are ephemeral — no session is saved, so there's
nothing to resume. Background runs instead persist a session per run (or per
chain step) specifically so they can be resumed later. This requires
treating the run's working directory as trusted (`--approve`) purely so
session persistence isn't silently skipped by Tau's project-trust gate —
it does not affect which tools/agents are available, that's still governed
entirely by `agent_scope`/`target_scope`/`confirm_project_agents` above.

`run_id` accepts a unique prefix, so you don't need to quote the full id back.

There's no separate `steer` action for nudging a still-running child without
stopping it — that would need a bidirectional channel to the child process
that Tau's CLI doesn't expose yet. `resume` covers the same need by
interrupting first, then continuing with the new message.

## Managing agent definitions

Instead of `spawn`/`chain`, pass `action` to manage agent definitions directly
(mutually exclusive with `spawn`/`chain`). If the model is unsure what agents
exist, it should call `{"action": "list"}` first — this is how it discovers
the current roster; nothing is baked into the tool's schema ahead of time.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `list` | `{action: "list"}` | Discover currently configured agents |
| `get` | `{action: "get", agent}` | Full detail on one agent |
| `create` | `{action: "create", agent, config: {description, system_prompt, tools?, model?}, target_scope?}` | Define a new agent |
| `update` | `{action: "update", agent, config: {...}, target_scope?}` | Edit an existing agent — only given fields change |
| `delete` | `{action: "delete", agent, target_scope?}` | Remove a custom agent |
| `eject` | `{action: "eject", agent, target_scope?}` | Copy an agent (from wherever it's currently defined, e.g. a builtin) into `target_scope` as an editable file that shadows the original |
| `disable` | `{action: "disable", agent, target_scope?}` | Hide an agent from discovery without deleting it |
| `enable` | `{action: "enable", agent, target_scope?}` | Restore a disabled agent |
| `reset` | `{action: "reset", agent, target_scope?}` | Delete the scope's custom file and clear any disabled override |

`target_scope` is `"user"` (default, `~/.tau/agents`) or `"project"`
(`.tau/agents`) — which directory gets written to. This is separate from
`agent_scope`, which controls which directories are *searched* for
`spawn`/`chain`/`list`/`get`.

`create`/`update`/`delete`/`eject`/`disable`/`enable`/`reset` targeting
`target_scope: "project"` prompt for confirmation first when running in an
interactive TUI session (same `confirm_project_agents` flag gates this as
running project agents) — writing an agent definition into the repo is a
repo-affecting side effect, same class of risk as running one.

There's no separate database — these actions just read/write the same
markdown files described below, plus a small `.disabled.json` per scope for
disable/enable.

## Agent definitions

Agents are markdown files with frontmatter:

```markdown
---
name: my-agent
description: What this agent does
tools: read, grep, glob, ls
model: claude-haiku-4-5
---

System prompt for the agent goes here.
```

Omitting `tools` gives the agent the default toolset; omitting `model` uses
whatever the parent session's model resolves to.

**Locations:**
- `~/.tau/agents/*.md` — user-level (always loaded)
- `.tau/agents/*.md` — project-level (only with `agent_scope: "project"` or `"both"`)

Project agents override user agents with the same name when `agent_scope: "both"`.

## Error handling

- Non-zero exit code, or a final `stop_reason` of `"error"`/`"abort"`, marks
  the step/task as failed.
- `chain` stops at the first failing step and reports which step failed.
- `spawn` always runs every task to completion and reports a per-task
  success/failure summary; per-task output shown to the parent model is
  capped at 50 KB.
- Ctrl+C (abort) kills the in-flight subagent process(es).

## Limitations

- No live tool-call-argument streaming widget — updates surface as plain text
  through the standard tool-result renderer (this mirrors how every other
  Tau tool renders, rather than adding a bespoke TUI component).
- Agents are discovered fresh on every invocation (safe to edit mid-session).
- `spawn` is capped at 8 tasks, 4 running concurrently.
