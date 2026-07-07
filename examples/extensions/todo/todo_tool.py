from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from todo_schema import TodoParams

from tau.tool.render import call_line
from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

CUSTOM_TYPE = "todo:state"


@dataclass
class TodoItem:
    id: int
    text: str
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "done": self.done}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        return cls(id=data["id"], text=data["text"], done=bool(data.get("done", False)))


class TodoState:
    """In-memory todo list, rebuilt from the session's custom entries on branch changes."""

    def __init__(self) -> None:
        self.items: list[TodoItem] = []
        self._next_id = 1

    def rebuild(self, entries: list[Any]) -> None:
        from tau.session.types import CustomInfoEntry

        self.items = []
        self._next_id = 1
        for entry in entries:
            if isinstance(entry, CustomInfoEntry) and entry.custom_type == CUSTOM_TYPE:
                data = entry.data or {}
                self.items = [TodoItem.from_dict(d) for d in data.get("items", [])]
                self._next_id = data.get("next_id", 1)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items], "next_id": self._next_id}

    def add(self, text: str) -> TodoItem:
        item = TodoItem(id=self._next_id, text=text)
        self._next_id += 1
        self.items.append(item)
        return item

    def toggle(self, item_id: int) -> TodoItem | None:
        for item in self.items:
            if item.id == item_id:
                item.done = not item.done
                return item
        return None

    def clear(self) -> None:
        self.items = []
        self._next_id = 1

    def render(self) -> str:
        if not self.items:
            return "(empty)"
        done = sum(1 for i in self.items if i.done)
        lines = [f"{done}/{len(self.items)} done"]
        for item in self.items:
            mark = "x" if item.done else " "
            lines.append(f"[{mark}] {item.id}. {item.text}")
        return "\n".join(lines)


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    action = args.get("action", "")
    detail = args.get("text") or (str(args["id"]) if args.get("id") is not None else "")
    return call_line("todo", action, detail)


def _render_result(content: str, opts: Any) -> list[str]:
    return content.splitlines() or [content]


class TodoTool(Tool):
    def __init__(self, state: TodoState, runtime_ref: Any) -> None:
        self._state = state
        self._runtime_ref = runtime_ref
        super().__init__(
            name="todo",
            description=(
                "Manage a simple todo list to track multi-step work in this session. "
                "Actions: 'list' shows all items, 'add' appends a new item (requires "
                "'text'), 'toggle' flips an item's done state (requires 'id'), 'clear' "
                "removes every item. State persists across branches and restarts."
            ),
            schema=TodoParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=_render_call,
            render_result=_render_result,
            render_shell="default",
        )

    def _persist(self) -> None:
        runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
        if runtime is None:
            return
        sm = getattr(runtime, "session_manager", None)
        if sm is None:
            return
        sm.append_custom_info(CUSTOM_TYPE, self._state.to_dict())

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = TodoParams.model_validate(invocation.params)
        state = self._state

        if params.action == "list":
            return ToolResult.ok(invocation.id, state.render())

        if params.action == "add":
            if not params.text:
                return ToolResult.error(invocation.id, "add requires 'text'")
            item = state.add(params.text)
            self._persist()
            return ToolResult.ok(invocation.id, f"Added #{item.id}: {item.text}\n\n{state.render()}")

        if params.action == "toggle":
            if params.id is None:
                return ToolResult.error(invocation.id, "toggle requires 'id'")
            item = state.toggle(params.id)
            if item is None:
                return ToolResult.error(invocation.id, f"No item with id {params.id}")
            self._persist()
            status = "done" if item.done else "not done"
            return ToolResult.ok(invocation.id, f"#{item.id} marked {status}\n\n{state.render()}")

        if params.action == "clear":
            state.clear()
            self._persist()
            return ToolResult.ok(invocation.id, "Todo list cleared")

        return ToolResult.error(invocation.id, f"Unknown action: {params.action}")
