from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

from .todo_schema import TodoParams

if TYPE_CHECKING:
    from tau.extensions.context import ExtensionContext

CUSTOM_TYPE = "todo:state"


TODO_STATUSES = ("pending", "in_progress", "done", "failed")


@dataclass
class TodoItem:
    id: int
    subject: str
    description: str | None = None
    status: str = "pending"  # one of TODO_STATUSES
    completion_order: int | None = None

    @property
    def done(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "completion_order": self.completion_order,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        status = data.get("status")
        if status not in TODO_STATUSES:
            # Migrate entries persisted before 'status' replaced the 'done' bool.
            status = "done" if data.get("done") else "pending"
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data.get("description"),
            status=status,
            completion_order=data.get("completion_order"),
        )

    def line(self) -> str:
        return f"[{self.status}] #{self.id} {self.subject}"

    def detail(self) -> str:
        lines = [f"#{self.id} [{self.status}] {self.subject}"]
        if self.description:
            lines.append("  " + self.description.replace("\n", "\n  "))
        return "\n".join(lines)


class TodoState:
    """In-memory todo list, rebuilt from the session's custom entries on branch changes."""

    def __init__(self) -> None:
        self.items: list[TodoItem] = []
        self._next_id = 1
        self._global_completions = 0

    def rebuild(self, entries: list[Any]) -> None:
        from tau.session.types import CustomInfoEntry

        self.items = []
        self._next_id = 1
        self._global_completions = 0
        for entry in entries:
            if isinstance(entry, CustomInfoEntry) and entry.custom_type == CUSTOM_TYPE:
                data = entry.data or {}
                self.items = [TodoItem.from_dict(d) for d in data.get("items", [])]
                self._next_id = data.get("next_id", 1)
                self._global_completions = data.get("global_completions", 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "next_id": self._next_id,
            "global_completions": self._global_completions,
        }

    def find(self, item_id: int) -> TodoItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def create(
        self, subject: str, description: str | None, *, after_id: int | None = None
    ) -> TodoItem:
        return self.create_many([(subject, description)], after_id=after_id)[0]

    def create_many(
        self, specs: list[tuple[str, str | None]], *, after_id: int | None = None
    ) -> list[TodoItem]:
        """Create tasks, optionally inserted as a contiguous block right after
        an existing task instead of appended at the end. Caller is responsible
        for validating after_id exists first — an unknown id here just falls
        back to appending, so validate before calling if that should error.
        """
        new_items: list[TodoItem] = []
        for subject, description in specs:
            new_items.append(
                TodoItem(id=self._next_id, subject=subject, description=description or None)
            )
            self._next_id += 1
        if after_id is None:
            self.items.extend(new_items)
        else:
            idx = next(
                (i for i, existing in enumerate(self.items) if existing.id == after_id), None
            )
            insert_at = (idx + 1) if idx is not None else len(self.items)
            self.items[insert_at:insert_at] = new_items
        return new_items

    def update(
        self,
        item_id: int,
        *,
        subject: str | None = None,
        description: str | None = None,
        append_note: str | None = None,
        status: str | None = None,
    ) -> TodoItem | None:
        item = self.find(item_id)
        if item is None:
            return None
        if subject is not None:
            item.subject = subject.strip()
        if description is not None:
            item.description = description.strip() or None
        if append_note is not None and append_note.strip():
            note = append_note.strip()
            item.description = f"{item.description}\n\n{note}" if item.description else note
        if status is not None:
            was_done = item.status == "done"
            item.status = status
            if not was_done and status == "done":
                item.completion_order = self._global_completions
                self._global_completions += 1
            elif was_done and status != "done":
                item.completion_order = None
        return item

    def delete(self, item_id: int) -> TodoItem | None:
        item = self.find(item_id)
        if item is not None:
            self.items.remove(item)
        return item

    def move(self, item_id: int, *, after_id: int | None) -> TodoItem | None:
        """Reposition an existing task without touching its id/status/history.

        after_id=None moves it to the end. Removing item_id before locating
        after_id's index means the target index always accounts for the
        item's own removal, so this is safe even if after_id no longer
        exists by the time this runs (falls back to the end).
        """
        item = self.find(item_id)
        if item is None:
            return None
        self.items.remove(item)
        if after_id is None:
            self.items.append(item)
        else:
            idx = next(
                (i for i, existing in enumerate(self.items) if existing.id == after_id), None
            )
            insert_at = (idx + 1) if idx is not None else len(self.items)
            self.items.insert(insert_at, item)
        return item

    def clear(self) -> None:
        self.items = []
        self._next_id = 1
        self._global_completions = 0

    # builtins.list: the method name shadows the builtin inside this class scope
    def list(self, status_filter: str | None) -> builtins.list[TodoItem]:
        if status_filter in TODO_STATUSES:
            return [i for i in self.items if i.status == status_filter]
        return list(self.items)

    def remaining(self) -> builtins.list[TodoItem]:
        """Tasks still needing action: not yet done or failed."""
        return [i for i in self.items if i.status in ("pending", "in_progress")]

    def next_remaining(self) -> TodoItem | None:
        """The task to work on next: the active in_progress one, else the first pending."""
        in_progress = self.list("in_progress")
        if in_progress:
            return in_progress[0]
        pending = self.list("pending")
        return pending[0] if pending else None


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    from tau.tool.render import call_line

    action = args.get("action", "")
    detail = ""
    if action == "create":
        tasks = args.get("tasks")
        detail = f"{len(tasks)} tasks" if tasks else (args.get("subject") or "")
        if args.get("after_id") is not None:
            detail += f" after #{args['after_id']}"
    elif action == "list":
        detail = f"filter={args['filter']}" if args.get("filter") else ""
    elif args.get("id") is not None:
        detail = f"#{args['id']}"
        if action == "update" and args.get("status"):
            detail += f" status={args['status']}"
        if action == "update" and args.get("after_id") is not None:
            detail += f" after #{args['after_id']}"
    return call_line("todo", action, detail)


def _render_result(content: str, opts: Any) -> list[str]:
    # Minimal one-line confirmation, matching the pattern every real pi todo
    # extension uses (none of them hide the tool call/result — they all show
    # a compact per-action line). The board above the input is the persistent
    # overview, so 'list' shows a count instead of repeating the itemized
    # list already visible there; 'get' is the one place full detail earns
    # its keep, since the board doesn't show descriptions.
    theme = opts.theme
    lines = content.splitlines() or [content]
    action = opts.metadata.get("action")

    def _style(role: Any, text: str) -> str:
        if theme is None:
            return text
        from tau.tui.style import apply_style

        return apply_style(role, text)

    if opts.is_error:
        return [_style(theme.error if theme else None, f"✗ {lines[0]}")]

    if action == "list":
        count = 0 if content == "No tasks" else len(lines)
        text = "No tasks" if count == 0 else f"{count} task(s)"
        return [
            _style(theme.success if theme else None, "✓ ")
            + _style(theme.muted if theme else None, text)
        ]

    if action == "get":
        head = _style(theme.success if theme else None, "✓ ") + _style(
            theme.muted if theme else None, lines[0]
        )
        rest = [_style(theme.muted if theme else None, line) for line in lines[1:]]
        return [head, *rest]

    return [
        _style(theme.success if theme else None, "✓ ")
        + _style(theme.muted if theme else None, lines[0])
    ]


class TodoTool(Tool):
    def __init__(
        self,
        state: TodoState,
        runtime_ref: Any,
        on_mutate: Callable[[ExtensionContext], None] | None = None,
    ) -> None:
        self._state = state
        self._runtime_ref = runtime_ref
        self._on_mutate = on_mutate
        super().__init__(
            name="todo",
            description=(
                "Manage a todo list to track multi-step work in this session. Actions: "
                "'create' adds task(s) — pass 'tasks' (a list of {subject, description}) to "
                "write out a plan in one call instead of calling create repeatedly, or pass "
                "top-level 'subject'/'description' for a single task. Call 'create' again at "
                "any point (not just at the start) to add more steps as the plan grows or new "
                "work is discovered; pass 'after_id' to slot the new task(s) into the middle "
                "of the plan right after an existing task instead of appending at the end. "
                "'update' changes a task (requires 'id'; optional "
                "'subject', 'description' to replace it, 'append_note' to add a paragraph "
                "without replacing, 'status'='pending'|'in_progress'|'done'|'failed' to mark "
                "progress — use 'failed' when a task turns out to be blocked or not doable, "
                "not 'delete', so it stays visible instead of disappearing; 'after_id' "
                "repositions the task to sit right after another existing task, without "
                "touching its content or status — use this to reorder the plan instead of "
                "deleting and recreating a task); 'list' shows "
                "tasks, optionally filtered by "
                "'filter'='pending'|'in_progress'|'done'|'failed'; 'get' returns full detail "
                "for one task (requires 'id'); 'delete' removes a task (requires 'id'); "
                "'clear' removes every task. The list is shown to the user in a board above "
                "the input, not in the transcript, so don't repeat task contents back to the "
                "user after calling this tool. The board stays visible — even once every task "
                "is 'done'/'failed' — until 'clear' is called, so once the whole plan is "
                "complete, call 'clear' to dismiss it instead of leaving it up. State persists "
                "across branches and restarts. "
                "Never tell the user a task was created, updated, or completed unless you "
                "actually made the matching 'create'/'update' call in this turn — calling "
                "'list' alone does not change anything. If it's unclear which task or field "
                "to update, ask instead of guessing."
            ),
            schema=TodoParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=_render_call,
            render_result=_render_result,
            render_shell="default",
        )

    def _after_mutation(self) -> None:
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        sm = getattr(runtime, "session_manager", None)
        if sm is not None:
            sm.append_custom_info(CUSTOM_TYPE, self._state.to_dict())
        if self._on_mutate is not None:
            from tau.extensions.context import ExtensionContext

            self._on_mutate(ExtensionContext.from_runtime(runtime))

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = TodoParams.model_validate(invocation.params)
        state = self._state

        def _ok(content: str) -> ToolResult:
            return ToolResult.ok(invocation.id, content, metadata={"action": params.action})

        def _error(content: str) -> ToolResult:
            return ToolResult.error(invocation.id, content, metadata={"action": params.action})

        if params.action == "create":
            if params.after_id is not None and state.find(params.after_id) is None:
                return _error(f"after_id #{params.after_id} not found")
            if params.tasks:
                if any(not t.subject.strip() for t in params.tasks):
                    return _error("every task in 'tasks' needs a 'subject'")
                items = state.create_many(
                    [(t.subject.strip(), t.description) for t in params.tasks],
                    after_id=params.after_id,
                )
                self._after_mutation()
                summary = "\n".join(f"#{i.id}: {i.subject}" for i in items)
                return _ok(f"Created {len(items)} tasks\n{summary}")
            subject = (params.subject or "").strip()
            if not subject:
                return _error("create requires 'subject' or 'tasks'")
            item = state.create(subject, params.description, after_id=params.after_id)
            self._after_mutation()
            return _ok(f"Created #{item.id}: {item.subject}")

        if params.action == "update":
            if params.id is None:
                return _error("update requires 'id'")
            if state.find(params.id) is None:
                return _error(f"#{params.id} not found")
            if params.after_id is not None:
                if params.after_id == params.id:
                    return _error("after_id cannot be the same as id")
                if state.find(params.after_id) is None:
                    return _error(f"after_id #{params.after_id} not found")
            if (
                params.subject is None
                and params.description is None
                and params.append_note is None
                and params.status is None
                and params.after_id is None
            ):
                return _error(
                    "update requires at least one of 'subject', 'description', "
                    "'append_note', 'status', or 'after_id'",
                )
            updated = state.update(
                params.id,
                subject=params.subject,
                description=params.description,
                append_note=params.append_note,
                status=params.status,
            )
            if updated is None:
                return _error(f"#{params.id} not found")
            if params.after_id is not None:
                state.move(params.id, after_id=params.after_id)
            self._after_mutation()
            return _ok(f"Updated #{updated.id}")

        if params.action == "list":
            items = state.list(params.filter)
            content = "\n".join(i.line() for i in items) if items else "No tasks"
            return _ok(content)

        if params.action == "get":
            if params.id is None:
                return _error("get requires 'id'")
            found = state.find(params.id)
            if found is None:
                return _error(f"#{params.id} not found")
            return _ok(found.detail())

        if params.action == "delete":
            if params.id is None:
                return _error("delete requires 'id'")
            deleted = state.delete(params.id)
            if deleted is None:
                return _error(f"#{params.id} not found")
            self._after_mutation()
            return _ok(f"Deleted #{deleted.id}: {deleted.subject}")

        if params.action == "clear":
            count = len(state.items)
            state.clear()
            self._after_mutation()
            return _ok(f"Cleared {count} tasks")

        return _error(f"Unknown action: {params.action}")
