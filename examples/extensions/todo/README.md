# Todo

A todo list tool for tracking multi-step work, ported from the
[pi coding agent](https://github.com/badlogic/pi-mono)'s reference `todo.ts`
extension and [pi-todo-lite](https://github.com/JerryAZR/pi-todo-lite).

## Tool

Registers a single `todo` tool with six actions:

- `create` — add a task (`subject` required, optional `description`).
- `update` — change a task (`id` required; optional `subject`/`description` to
  replace them, `append_note` to add a paragraph without replacing, `done` to
  mark complete/incomplete).
- `list` — show tasks, optionally filtered by `filter: "done"|"pending"`.
- `get` — full detail for one task (`id` required).
- `delete` — remove a task (`id` required).
- `clear` — remove every task.

The task list is not echoed into the transcript — the tool's call/result
lines are collapsed to near-nothing (a blank spacer is the smallest footprint
Tau's renderer allows for a completed tool call; see the comments in
`todo_tool.py` for why full suppression isn't possible without touching core
rendering). The list itself is shown in a board above the input box instead.

## Board

A widget appears above the editor whenever there is at least one pending
task, and disappears the moment the list is empty or every task is done. It
updates immediately after every mutating action (`create`/`update`/`delete`/
`clear`) and re-syncs on `session_start`/`session_tree`/`tui_ready`.

## Commands

```text
/todos   show the current list, grouped by pending/done
```

## State

Todos are not stored in a separate file. Every mutation appends a
`todo:state` custom entry to the session log, and the full list is
reconstructed by replaying those entries on `session_start` and
`session_tree`. This means the list automatically reflects whatever branch
of the conversation you're on — fork or rewind, and the todos rewind with
it. Compaction doesn't affect this: Tau's `get_branch()` never drops entries,
it's only the LLM-facing prompt that gets trimmed.

Optional extension settings:

```json
{
  "enabled": true
}
```
