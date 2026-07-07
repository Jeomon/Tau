from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TodoParams(BaseModel):
    action: Literal["list", "add", "toggle", "clear"] = Field(
        ..., description="Which operation to perform on the todo list."
    )
    text: str | None = Field(
        default=None, description="Item text. Required for action='add'."
    )
    id: int | None = Field(
        default=None, description="Item id to toggle done/not-done. Required for action='toggle'."
    )
