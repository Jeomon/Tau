from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskItem(BaseModel):
    agent: str = Field(..., description="Name of the agent to invoke.")
    task: str = Field(..., description="Task to delegate to the agent.")
    cwd: str | None = Field(default=None, description="Working directory for the agent process.")


class ChainItem(BaseModel):
    agent: str = Field(..., description="Name of the agent to invoke.")
    task: str = Field(
        ...,
        description="Task with an optional '{previous}' placeholder for the prior step's output.",
    )
    cwd: str | None = Field(default=None, description="Working directory for the agent process.")


class SubagentParams(BaseModel):
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
    agent_scope: Literal["user", "project", "both"] = Field(
        default="user",
        description=(
            "Which agent directories to search. 'user' (default) only loads ~/.tau/agents. "
            "'project' or 'both' also load .tau/agents from the current project — only use "
            "this for trusted repositories."
        ),
    )
    confirm_project_agents: bool = Field(
        default=True,
        description="Prompt for confirmation before running project-local agents.",
    )
