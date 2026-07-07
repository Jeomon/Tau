from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TodoParams(BaseModel):
    action: Literal["create", "update", "list", "get", "delete", "clear"] = Field(
        ..., description="Which operation to perform on the todo list."
    )
    id: int | None = Field(
        default=None,
        description="Task id. Required for action='update'|'get'|'delete'.",
    )
    subject: str | None = Field(
        default=None,
        description=(
            "Short imperative subject line. Required for action='create'; "
            "replaces the existing subject for action='update'."
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
    done: bool | None = Field(
        default=None, description="Mark the task done or not done. Only used by action='update'."
    )
    filter: Literal["done", "pending"] | None = Field(
        default=None, description="Restrict action='list' to only done or only pending tasks."
    )
