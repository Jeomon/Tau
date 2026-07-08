from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


_CONTEXT_DESCRIPTION = (
    "'fresh' starts the subagent with no history — only its task. 'fork' "
    "resumes the parent session's current conversation as read-only context: "
    "the subagent sees everything so far, but nothing it does is ever "
    "written back to the parent session or anywhere else. Leave unset to "
    "use the run-level 'context', or the target agent's own default "
    "(falling back to 'fresh' if neither is set)."
)


class TaskItem(BaseModel):
    agent: str = Field(..., description="Name of the agent to invoke.")
    task: str = Field(..., description="Task to delegate to the agent.")
    cwd: str | None = Field(default=None, description="Working directory for the agent process.")
    context: Literal["fresh", "fork"] | None = Field(
        default=None, description=_CONTEXT_DESCRIPTION
    )


class ChainItem(BaseModel):
    agent: str = Field(..., description="Name of the agent to invoke.")
    task: str = Field(
        ...,
        description="Task with an optional '{previous}' placeholder for the prior step's output.",
    )
    cwd: str | None = Field(default=None, description="Working directory for the agent process.")
    context: Literal["fresh", "fork"] | None = Field(
        default=None, description=_CONTEXT_DESCRIPTION
    )


class SubagentParams(BaseModel):
    action: Literal["list", "get", "tasks"] | None = Field(
        default=None,
        description=(
            "What to do. 'list' (default when 'spawn'/'chain' are both omitted) returns "
            "every discovered agent's name, source, description, tools, and model — call "
            "this first if unsure what agents exist. 'get' returns full detail (including "
            "the system prompt) for one agent named in 'agent'. 'tasks' runs 'spawn' and/or "
            "'chain' — it's the implicit action whenever either is provided, so you rarely "
            "need to set it explicitly."
        ),
    )
    agent: str | None = Field(
        default=None, description="Target agent name for action='get'."
    )
    spawn: list[TaskItem] | None = Field(
        default=None,
        description=(
            "Tasks to run concurrently, each in its own isolated agent process. A single "
            "task is just a one-item list. Max 8, 4 at a time."
        ),
    )
    chain: list[ChainItem] | None = Field(
        default=None,
        description=(
            "Steps to run one after another. Each step's 'task' may include the literal "
            "placeholder '{previous}', replaced with the prior step's final output."
        ),
    )
    context: Literal["fresh", "fork"] | None = Field(
        default=None,
        description=(
            "Default context mode for every task/step that doesn't set its own "
            "'context' (still overridden by each agent's own default when this is "
            "left unset). " + _CONTEXT_DESCRIPTION
        ),
    )
