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
