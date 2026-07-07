from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TodoTaskInput(BaseModel):
    subject: str = Field(..., description="Short imperative subject line.")
    description: str | None = Field(default=None, description="Long-form task description.")


class TodoParams(BaseModel):
    action: Literal["create", "update", "list", "get", "delete", "clear"] = Field(
        ..., description="Which operation to perform on the todo list."
    )
    id: int | None = Field(
        default=None,
        description="Task id. Required for action='update'|'get'|'delete'.",
    )
    tasks: list[TodoTaskInput] | None = Field(
        default=None,
        description=(
            "For action='create': a batch of tasks to add in one call, each with its own "
            "'subject' and optional 'description'. Use this to write out the full plan at "
            "once instead of calling create repeatedly. Takes precedence over the top-level "
            "'subject'/'description' fields if both are given."
        ),
    )
    after_id: int | None = Field(
        default=None,
        description=(
            "For action='create': insert the new task(s) immediately after this existing "
            "task id instead of appending at the end. For action='update': reposition the "
            "task being updated to sit immediately after this id, without touching its "
            "content or status — use this to reorder the plan instead of deleting and "
            "recreating a task. Omit to append at the end (create) or leave the position "
            "unchanged (update)."
        ),
    )
    subject: str | None = Field(
        default=None,
        description=(
            "Short imperative subject line for a single task. Required for action='create' "
            "when 'tasks' is not used; replaces the existing subject for action='update'."
        ),
    )
    description: str | None = Field(
        default=None,
        description=(
            "Long-form task description. For action='update' this replaces the entire "
            "existing description — use append_note to add without replacing."
        ),
    )
    append_note: str | None = Field(
        default=None,
        description="Append a paragraph to the task's existing description. Only used by action='update'.",
    )
    status: Literal["pending", "in_progress", "done", "failed"] | None = Field(
        default=None,
        description=(
            "New status for the task. Only used by action='update'. Set 'in_progress' when "
            "you start actively working on it, 'done' when finished, 'failed' if it turns "
            "out to be blocked or not doable (keeps it visible instead of deleting it)."
        ),
    )
    filter: Literal["pending", "in_progress", "done", "failed"] | None = Field(
        default=None, description="Restrict action='list' to tasks with this status."
    )
