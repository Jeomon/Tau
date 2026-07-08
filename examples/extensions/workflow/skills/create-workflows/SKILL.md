---
name: create-workflows
description: Author declarative .tau/workflows/*.yaml files for Tau's /workflow command — reusable multi-phase, multi-agent pipelines. Use when the user asks to create, write, edit, or scaffold a workflow, or wants to turn a repeated multi-step task (audits, migrations, reviews, research) into a rerunnable automation.
---

# Create Workflows

A workflow is a static YAML file, not a script. There is no code execution,
no loops/conditionals, and no LLM tool call involved in running one — the
`/workflow` extension parses the file and runs one isolated in-process
subagent per task (its own Engine/LLM/tools, no OS subprocess, no shared
session with the parent), in the order and grouping the file declares.

## Before writing one

Call the `subagent` tool with `action='list'` to see which agent names are
actually available in this project (builtins like `scout`, `worker`,
`reviewer`, `planner`, `oracle`, `delegate`, `researcher`, `context-builder`,
plus any project agents in `.tau/agents/` or user agents in `~/.tau/agents/`).
Only reference agent names that exist — an unknown agent name fails the whole
run at that task.

Every task's agent only has access to the base coding tools it declares from
`read`, `write`, `edit`, `terminal`, `glob`, `grep`, `ls` — extension tools
like `web_search`, `web_fetch`, `subagent`, or `todo` are never available
inside a workflow task, regardless of what the agent's own frontmatter
`tools:` lists. Write tasks that fetch URLs via `terminal` (e.g. `curl`), not
`web_fetch`.

## File location and identity

Save workflows to `.tau/workflows/<slug>.yaml` in the project. The filename
stem is the workflow's identifier for `/workflow <name>`, so use the same
lowercase-hyphen slug as `meta.name`.

## Shape

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

- `meta.name` and `meta.description` are required non-empty strings.
- `enabled` is optional, defaults `true`. A disabled workflow can't be run
  until re-enabled via `/workflow`.
- `phases` is a required non-empty ordered list. Each phase has a required
  `title` and a required non-empty `tasks` list.
- Each task has a required `agent` (must match a discovered agent name) and
  `task` (the prompt text, may include placeholders below). `label` is
  optional but required if a later task needs to reference this one's output.

## Sequencing a phase

- Default (no `parallel`, no `for_each`): tasks run one after another. Each
  task's output becomes `{previous}` for the next task, and is stored under
  its `label` (or an auto label) for `{results.<label>}` lookups from any
  later phase.
- `parallel: true` with an explicit `tasks` list: every task in the phase
  runs concurrently. They cannot see each other's output (all read the
  state as of phase start) — use this for independent tasks only, then a
  later phase to synthesize.
- `for_each: "{previous}"` or `for_each: "{results.<label>}"` with exactly
  one task template: the referenced result is parsed as a JSON array (or, if
  not valid JSON, split into non-empty lines) and the single task runs once
  per item, with `{item}` substituted. Combine with `parallel: true` to fan
  out concurrently, or omit it to run the items one at a time.

## Placeholders

- `{previous}` — the immediately prior task's (or phase's) output text.
- `{results.<label>}` — a specific earlier task's output, by its `label`.
  For a `for_each` phase, the aggregate output is a JSON array stored under
  that phase's label, so it can be fed into a further `for_each`.
- `{item}` — current item inside a `for_each` phase only.

## Structured output

Give a task an optional `schema` (a flat JSON Schema object) when its output
feeds a later `for_each` or `{results.<label>}` and needs to be reliably
parseable — models often wrap plain-text JSON in commentary or code fences,
which breaks `for_each`'s parser. With `schema` set, the task gets a
`structured_output` tool it must call exactly once as its final action; the
validated call arguments (as compact JSON, no wrapping) become the task's
output instead of whatever prose the model would otherwise write. A task
that finishes without calling it fails, like any other task failure.

Only flat shapes are supported: `string`, `integer`, `number`, `boolean`,
and arrays of those. Nested `object` types aren't validated (they pass
through unconstrained) — keep schemas flat.

```yaml
- title: Extract Findings
  tasks:
    - agent: reviewer
      task: "List every file with a missing auth check, and why."
      label: findings
      schema:
        type: object
        properties:
          files:
            type: array
            items: { type: string }
          reason:
            type: string
        required: [files, reason]
```

## Failure handling

Any task failure aborts the whole run immediately (fail-fast) — there is no
partial-success mode. Design phases so a task that must not block the rest
of the pipeline runs in its own low-stakes phase, or accept that a failure
there should indeed stop the run.

## After writing the file

Tell the user to run it with `/workflow <name>`, or open `/workflow` (no
args) to find it in the picker, where they can also disable, rename, or
delete it. Do not try to run a workflow yourself by writing to the file and
executing `tau` directly — `/workflow` is the only entry point.
