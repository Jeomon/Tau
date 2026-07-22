# workflow

A single `/workflow` command for running declarative, multi-agent pipelines
defined as YAML files under `.tau/workflows/`.

Modeled on the "dynamic workflows" idea from Claude Code and Pi's
[pi-dynamic-workflows](https://github.com/michaelliv/pi-dynamic-workflows),
but trimmed down: no scripting sandbox, no LLM-authored script, no tool call.
A workflow here is a static file — an ordered list of phases, each running
one or more subagent tasks — parsed and executed directly by this extension.

## Usage

```
/workflow                 open the workflow manager
/workflow <name>           run a workflow directly by name
```

Bare `/workflow` opens a picker listing every workflow as
`■/☐ slug  (n phases)  —  <description>` (■ = enabled, ☐ = disabled), plus any
unparseable file flagged with `⚠ <filename> — parse error`. Selecting a valid
workflow opens an action menu: **Run**, **Enable/Disable**, **Rename**,
**Delete**, or **Back**. The list also has **+ New workflow**, which prompts
for a name, writes a starter YAML file, and opens it in the editor. In
headless mode (no TUI), bare `/workflow` just prints the current list as
text since there's no picker to drive.

## Workflow file shape

```yaml
meta:
  name: audit-routes
  description: Audit route handlers for missing auth checks

enabled: true

phases:
  - title: Scan
    tasks:
      - agent: scout
        task: "List every .ts file under src/routes/"
        label: file-list

  - title: Audit
    parallel: true
    for_each: "{results.file-list}"
    tasks:
      - agent: reviewer
        task: "Audit {item} for missing authentication checks"

  - title: Synthesize
    tasks:
      - agent: worker
        task: "Merge findings: {previous}"
        label: summary
```

- Each phase's tasks run sequentially by default (chaining `{previous}` and
  `{results.<label>}`), or concurrently with `parallel: true`.
- `for_each` fans a single task template out over a prior result — parsed as
  a JSON array, or split into non-empty lines — substituting `{item}`.
- Any task failure aborts the run immediately (fail-fast): there is no
  partial-success mode.
- Esc (or Ctrl+C) cancels a running workflow: no new task starts, in-flight
  tasks abort cooperatively, and the run reports how many tasks completed
  before the abort. (Wired through `ctx.command_signal`, the ambient
  per-command abort signal.)
- `agent` names are resolved the same way the `subagent` tool resolves them:
  builtins (`scout`, `worker`, `reviewer`, `planner`, `oracle`, `delegate`,
  `researcher`, `context-builder`), plus project (`.tau/agents/`) and user
  (`~/.tau/agents/`) agents.

Every task runs an isolated in-process agent (`run_embedded_agent`, the same
mechanism the `subagent` tool uses) with its own Engine/LLM/tools — no OS
subprocess, no shared session state with the parent, and no LLM tool call
involved in running the workflow itself.

## Skill

This extension bundles a `create-workflows` skill (`skills/create-workflows/`)
that teaches the model the YAML schema so it can author `.tau/workflows/*.yaml`
files on request. It's registered directly on `session_start` via the
skill registry's `register()` — not through the `resources_discover` hook,
which fires before extensions load and would always be one reload late.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Entry point — `register(tau)`, the `/workflow` command handler, the interactive picker/action-menu flow, skill registration |
| `model.py` | Parses and validates `.tau/workflows/*.yaml` into `WorkflowDef`/`WorkflowPhase`/`WorkflowTask` |
| `store.py` | Discovery, create/rename/delete/enable-disable file operations |
| `runner.py` | Execution engine — phase sequencing, placeholder rendering, `for_each`/`parallel` fan-out, subagent process spawning |
| `agents.py` | Reuses the `subagent` extension's agent discovery (loaded by file path, no import-order dependency) |
| `skills/create-workflows/SKILL.md` | Teaches the model how to author workflow YAML files |
| `manifest.json` | Settings-panel toggle (`enabled`) |
