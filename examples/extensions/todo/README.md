# Todo

A todo list tool for tracking multi-step work, ported from the
[pi coding agent](https://github.com/badlogic/pi-mono)'s reference `todo.ts`
extension and [pi-todo-lite](https://github.com/JerryAZR/pi-todo-lite).

## Tool

Registers a single `todo` tool with six actions:

- `create` — add task(s). Pass `tasks` (a list of `{subject, description}`) to
  write out a plan in one call, or top-level `subject`/`description` for a
  single task. Callable again at any point to add more steps as the plan
  grows.
- `update` — change a task (`id` required; optional `subject`/`description` to
  replace them, `append_note` to add a paragraph without replacing, `status`
  to move it through `pending` → `in_progress` → `done`/`failed`).
- `list` — show tasks, optionally filtered by `filter: "pending"|"in_progress"|"done"|"failed"`.
- `get` — full detail for one task (`id` required).
- `delete` — remove a task (`id` required).
- `clear` — remove every task.

Each task carries a `status` (`pending`/`in_progress`/`done`/`failed`) instead
of a plain done flag — `failed` keeps a task visible instead of deleting it
when it turns out to be blocked or not doable.

The task list is not echoed into the transcript — the tool's call/result
lines are collapsed to near-nothing (a blank spacer is the smallest footprint
Tau's renderer allows for a completed tool call; see the comments in
`todo_tool.py` for why full suppression isn't possible without touching core
rendering). The list itself is shown in a board above the input box instead.

## Board

A widget appears above the editor whenever there is at least one task that
isn't done or failed, and disappears once every task is resolved or the list
is empty. It updates immediately after every mutating action
(`create`/`update`/`delete`/`clear`) and re-syncs on
`session_start`/`session_tree`/`tui_ready`. Glyphs and colors come from the
active theme (`☐` pending/muted, `■` in_progress/warning, `✓` done/success,
`✗` failed/error) via `apply_style`, not hardcoded ANSI.

## Commands

```text
/todos   show the current list, grouped by pending/in progress/done/failed
```

## State

Todos are not stored in a separate file. Every mutation appends a
`todo:state` custom entry to the session log, and the full list is
reconstructed by replaying those entries on `session_start` and
`session_tree`. This means the list automatically reflects whatever branch
of the conversation you're on — fork or rewind, and the todos rewind with
it. Compaction doesn't affect this: Tau's `get_branch()` never drops entries,
it's only the LLM-facing prompt that gets trimmed. Sessions persisted before
the `status` field existed (plain `done` bool) migrate automatically on
replay.

The agent is not forced to keep working through pending tasks between
turns — it decides on its own whether to continue, same as any other tool.

Optional extension settings:

```json
{
  "enabled": true
}
```
