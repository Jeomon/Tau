from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from todo_schema import TodoParams  # type: ignore[import-not-found]

from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

if TYPE_CHECKING:
    from tau.extensions.context import ExtensionContext

CUSTOM_TYPE = "todo:state"


@dataclass
class TodoItem:
    id: int
    subject: str
    description: str | None = None
    done: bool = False
    completion_order: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "done": self.done,
            "completion_order": self.completion_order,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data.get("description"),
            done=bool(data.get("done", False)),
            completion_order=data.get("completion_order"),
        )

    def line(self) -> str:
        status = "done" if self.done else "pending"
        return f"[{status}] #{self.id} {self.subject}"

    def detail(self) -> str:
        lines = [f"#{self.id} [{'done' if self.done else 'pending'}] {self.subject}"]
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

    def create(self, subject: str, description: str | None) -> TodoItem:
        item = TodoItem(id=self._next_id, subject=subject, description=description or None)
        self._next_id += 1
        self.items.append(item)
        return item

    def update(
        self,
        item_id: int,
        *,
        subject: str | None = None,
        description: str | None = None,
        append_note: str | None = None,
        done: bool | None = None,
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
        if done is not None:
            was_done = item.done
            item.done = done
            if not was_done and done:
                item.completion_order = self._global_completions
                self._global_completions += 1
            elif was_done and not done:
                item.completion_order = None
        return item

    def delete(self, item_id: int) -> TodoItem | None:
        item = self.find(item_id)
        if item is not None:
            self.items.remove(item)
        return item

    def clear(self) -> None:
        self.items = []
        self._next_id = 1
        self._global_completions = 0

    def list(self, status_filter: str | None) -> list[TodoItem]:
        if status_filter == "done":
            return [i for i in self.items if i.done]
        if status_filter == "pending":
            return [i for i in self.items if not i.done]
        return list(self.items)


def _render_call(_args: dict, _streaming: bool = False) -> list[str]:
    # The task list lives in the above-editor board, not the transcript — return
    # no lines so message_list.py skips the call entirely (see comment on
    # TodoTool.render_result below for why the result can't vanish the same way).
    return []


def _render_result(_content: str, _opts: Any) -> list[str]:
    # message_list.py falls back to rendering raw content when a custom
    # render_result returns an empty list, so full suppression isn't possible
    # here — a single blank line is the smallest footprint a non-empty return
    # can produce with render_shell="self" (no framing applied).
    return [""]


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
                "'create' adds a task (requires 'subject', optional 'description'); "
                "'update' changes a task (requires 'id'; optional 'subject', 'description' "
                "to replace it, 'append_note' to add a paragraph without replacing, 'done' "
                "to mark complete/incomplete); 'list' shows tasks, optionally filtered by "
                "'filter'='done'|'pending'; 'get' returns full detail for one task (requires "
                "'id'); 'delete' removes a task (requires 'id'); 'clear' removes every task. "
                "The list is shown to the user in a board above the input, not in the "
                "transcript, so don't repeat task contents back to the user after "
                "calling this tool. State persists across branches and restarts."
            ),
            schema=TodoParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=_render_call,
            render_result=_render_result,
            render_shell="self",
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

        if params.action == "create":
            subject = (params.subject or "").strip()
            if not subject:
                return ToolResult.error(invocation.id, "create requires 'subject'")
            item = state.create(subject, params.description)
            self._after_mutation()
            return ToolResult.ok(invocation.id, f"Created #{item.id}: {item.subject}")

        if params.action == "update":
            if params.id is None:
                return ToolResult.error(invocation.id, "update requires 'id'")
            if state.find(params.id) is None:
                return ToolResult.error(invocation.id, f"#{params.id} not found")
            if (
                params.subject is None
                and params.description is None
                and params.append_note is None
                and params.done is None
            ):
                return ToolResult.error(
                    invocation.id,
                    "update requires at least one of 'subject', 'description', "
                    "'append_note', or 'done'",
                )
            item = state.update(
                params.id,
                subject=params.subject,
                description=params.description,
                append_note=params.append_note,
                done=params.done,
            )
            if item is None:
                return ToolResult.error(invocation.id, f"#{params.id} not found")
            self._after_mutation()
            return ToolResult.ok(invocation.id, f"Updated #{item.id}")

        if params.action == "list":
            items = state.list(params.filter)
            content = "\n".join(i.line() for i in items) if items else "No tasks"
            return ToolResult.ok(invocation.id, content)

        if params.action == "get":
            if params.id is None:
                return ToolResult.error(invocation.id, "get requires 'id'")
            item = state.find(params.id)
            if item is None:
                return ToolResult.error(invocation.id, f"#{params.id} not found")
            return ToolResult.ok(invocation.id, item.detail())

        if params.action == "delete":
            if params.id is None:
                return ToolResult.error(invocation.id, "delete requires 'id'")
            item = state.delete(params.id)
            if item is None:
                return ToolResult.error(invocation.id, f"#{params.id} not found")
            self._after_mutation()
            return ToolResult.ok(invocation.id, f"Deleted #{item.id}: {item.subject}")

        if params.action == "clear":
            count = len(state.items)
            state.clear()
            self._after_mutation()
            return ToolResult.ok(invocation.id, f"Cleared {count} tasks")

        return ToolResult.error(invocation.id, f"Unknown action: {params.action}")
