# Subagent Example

Delegate tasks to specialized subagents with isolated context windows.

## Features

- **Isolated context**: each subagent runs in a separate `tau` process
- **Streaming output**: see tool calls and progress as they happen
- **Parallel streaming**: all parallel tasks stream updates simultaneously
- **Usage tracking**: shows turns, tokens, and cost per agent
- **Abort support**: aborting the parent turn kills the subagent process(es)

## Structure

```
subagent/
├── README.md            # This file
├── __init__.py           # Registers the tool
├── subagent_tool.py       # The `subagent` tool (execute + render)
├── subagent_schema.py     # Pydantic params schema
├── agents.py              # Agent discovery logic
└── agents/                # Sample agent definitions
    ├── scout.md              # Fast recon, returns compressed context
    ├── researcher.md          # Web/docs research with sources
    ├── planner.md              # Creates implementation plans
    ├── context-builder.md       # Requirements + code recon -> handoff brief
    ├── oracle.md                 # Second opinion / drift check, no edits
    ├── worker.md                  # General-purpose (full capabilities)
    ├── reviewer.md                 # Code review (read-only, suggests fixes)
    └── delegate.md                  # Lightweight full-access, no fixed persona
```

## Installation

This extension ships as a Tau builtin (`tau/builtins/extensions/subagent`),
so it's already registered — this directory is the annotated example to
read or fork. To run a modified copy instead, symlink it into your user
extensions directory:

```bash
mkdir -p ~/.tau/extensions
ln -sf "$(pwd)/examples/extensions/subagent" ~/.tau/extensions/subagent
```

## Security Model

This tool executes a separate `tau` subprocess with a delegated system
prompt and tool/model configuration.

**Project-local agents** (`.tau/agents/*.md`) are repo-controlled prompts
that can instruct the model to read files, run shell commands, etc.
Discovery always merges all three tiers (project + user + builtin) — there
is no scope switch to opt into seeing project agents, since `list`/`get`
are read-only and don't execute anything.

The safety gate is at *execution* time instead: when running interactively,
the tool prompts for confirmation before actually running any agent
sourced from `.tau/agents` (`confirm_project_agents: false` to disable).
Only approve this for repositories you trust.

## Usage

### Browse agents
```
Show me the available subagents
```

### Agent detail
```
Show me the full system prompt for oracle
```

### Single agent
```
Use scout to find all authentication code
```

### Parallel execution
```
Run 2 scouts in parallel: one to find models, one to find providers
```

### Chained workflow
```
Use a chain: first have scout find the read tool, then have planner suggest improvements
```

## Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `list` | *(none — this is the default when 'spawn'/'chain' are both omitted)* | Every discovered agent's name, source, description, tools, and model |
| `get` | `agent` | Full detail for one agent, including its system prompt |
| `tasks` | `spawn` and/or `chain` *(implicit whenever either is set)* | Execute — see Tool Modes below |

## Tool Modes

| Mode | Parameter | Description |
|------|-----------|-------------|
| Spawn | `{spawn: [...]}` | One or more agents run concurrently (max 8, 4 concurrent) — a single task is just a one-item list |
| Chain | `{chain: [...]}` | Sequential with `{previous}` placeholder |

## Agent Definitions

Agents are markdown files with YAML frontmatter:

```markdown
---
name: my-agent
description: What this agent does
tools: read, grep, glob, ls
---

System prompt for the agent goes here.
```

**Locations, always merged in priority order (highest wins):**
- `.tau/agents/*.md` — project-level (repo-controlled; confirmed before running, see Security Model)
- `~/.tau/agents/*.md` — user-level
- `agents/*.md` in this extension — builtin samples (lowest priority)

## Sample Agents

| Agent | Purpose | Tools |
|-------|---------|-------|
| `scout` | Fast codebase recon | read, grep, glob, ls, terminal |
| `researcher` | Web/docs research with sources | read, web_search, web_fetch |
| `planner` | Implementation plans | read, grep, glob, ls |
| `context-builder` | Requirements + code recon, produces a handoff brief | read, grep, glob, ls, terminal, web_search |
| `oracle` | Second opinion — checks for drift and hidden assumptions, never edits | read, grep, glob, ls, terminal |
| `worker` | General-purpose implementation | read, grep, glob, ls, terminal, edit, write |
| `reviewer` | Code review — read-only, suggests fixes | read, grep, glob, ls, terminal |
| `delegate` | Lightweight full-access, no fixed persona | read, grep, glob, ls, terminal, edit, write |

`researcher` and `context-builder`'s web tools require the `web` extension
(`web_search`/`web_fetch`) to be enabled.

## Model Selection

Every subagent inherits the parent session's current model — there is no
per-agent model override yet. An agent's `model:` frontmatter field is
parsed but only used as a fallback when the parent session has no model
resolved (e.g. a subagent spawned outside a running session). This keeps
the extension model-independent for now instead of hardcoding a
Haiku/Sonnet-style split per role.

## Error Handling

- **Exit code != 0**: tool returns error with stderr/output
- **stop_reason "error"**: LLM error propagated with error message
- **stop_reason "abort"**: abort kills the subprocess, throws error
- **Chain mode**: stops at the first failing step, reports which step failed

## Limitations

- Runs are ephemeral — no session is saved for the subagent process, and
  there's no background/async mode; the tool call blocks until its
  subagent(s) finish.
- Parallel/chain output per task is capped at 50 KB in the model-visible
  summary.
- Agents are discovered fresh on each invocation (safe to edit mid-session).
- Parallel mode is limited to 8 tasks, 4 concurrent.
