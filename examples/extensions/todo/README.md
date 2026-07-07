# Todo

A simple todo list tool for tracking multi-step work, ported from the
[pi coding agent](https://github.com/badlogic/pi-mono)'s reference `todo.ts`
extension example.

## Tool

Registers a `todo` tool with four actions:

- `list` — show all items with done/total counts.
- `add` — append a new item (`text` required).
- `toggle` — flip an item's done state (`id` required).
- `clear` — remove every item.

## Commands

```text
/todos   show the current list
```

## State

Todos are not stored in a separate file. Every mutation appends a
`todo:state` custom entry to the session log, and the full list is
reconstructed by replaying those entries on `session_start` and
`session_tree`. This means the list automatically reflects whatever branch
of the conversation you're on — fork or rewind, and the todos rewind with
it.

Optional extension settings:

```json
{
  "enabled": true
}
```
