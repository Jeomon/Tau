from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class AgentDefinition(BaseModel):
    description: str | None = Field(default=None, description="What this agent does.")
    system_prompt: str | None = Field(default=None, description="The agent's system prompt.")
    tools: str | None = Field(
        default=None, description="Comma-separated tool allowlist, e.g. 'read, grep, glob, ls'."
    )
    model: str | None = Field(default=None, description="Model id override for this agent.")


ManagementAction = Literal[
    "list",
    "get",
    "create",
    "update",
    "delete",
    "eject",
    "disable",
    "enable",
    "reset",
    "status",
    "interrupt",
    "resume",
]


class SubagentParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    spawn: list[TaskItem] | None = Field(
        default=None,
        description=(
            "Tasks to run concurrently, each in its own isolated agent process. Max 8, "
            "4 at a time. A single task is just a one-item list."
        ),
    )
    chain: list[ChainItem] | None = Field(
        default=None,
        description=(
            "Steps to run one after another. Each step's 'task' may include the literal "
            "placeholder '{previous}', replaced with the prior step's final output. A "
            "single step is just a one-item list."
        ),
    )
    action: ManagementAction | None = Field(
        default=None,
        description=(
            "Management action instead of execution — omit 'spawn'/'chain' when set. "
            "'list' discovers currently configured agents (call this first if unsure "
            "what agents exist); 'get' returns full detail on one ('agent' required); "
            "'create' defines a new agent ('agent' + 'config' required); 'update' edits "
            "an existing one ('agent' + 'config', only given fields change); 'delete' "
            "removes a custom agent ('agent' required); 'eject' copies an agent from "
            "wherever it's currently defined into 'target_scope' as an editable file "
            "that shadows it ('agent' required); 'disable'/'enable' hide/restore an "
            "agent without deleting it ('agent' required); 'reset' deletes the custom "
            "file and clears any disabled override for 'target_scope', reverting to "
            "whatever (if anything) is defined elsewhere ('agent' required). "
            "'status' reports on a background run started with async=true ('run_id' "
            "required, or omitted to list every tracked run); 'interrupt' stops a "
            "running background run, keeping its partial output ('run_id' required); "
            "'resume' continues a finished or interrupted background run with a new "
            "message, interrupting it first if it's still running ('run_id' + "
            "'message' required)."
        ),
    )
    agent: str | None = Field(
        default=None,
        description="Target agent name for get/create/update/delete/eject/disable/enable/reset.",
    )
    config: AgentDefinition | None = Field(
        default=None, description="Agent definition fields for create/update."
    )
    run_async: bool = Field(
        default=False,
        alias="async",
        description=(
            "For 'spawn'/'chain' execution only: return immediately with a run id per "
            "spawned task (or one run id for the whole chain) instead of waiting for "
            "completion. Check on it later with action='status'/'interrupt'/'resume'."
        ),
    )
    run_id: str | None = Field(
        default=None,
        description="Target run id (or unique prefix) for status/interrupt/resume.",
    )
    message: str | None = Field(default=None, description="Follow-up message for 'resume'.")
    target_scope: Literal["user", "project"] = Field(
        default="user",
        description=(
            "Which agents directory to write to for create/update/delete/eject/disable/"
            "enable/reset — '~/.tau/agents' (user) or '.tau/agents' (project)."
        ),
    )
    agent_scope: Literal["user", "project", "both"] = Field(
        default="user",
        description=(
            "Which agent directories to search for execution and 'list'. 'user' "
            "(default) only loads ~/.tau/agents. 'project' or 'both' also load "
            ".tau/agents from the current project — only use this for trusted "
            "repositories."
        ),
    )
    confirm_project_agents: bool = Field(
        default=True,
        description="Prompt for confirmation before running project-local agents.",
    )
