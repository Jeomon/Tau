from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

if TYPE_CHECKING:
    from .manager import SubagentManager


# ── Schemas ────────────────────────────────────────────────────────────────────


class AgentInput(BaseModel):
    prompt: str = Field(description="The task for the agent to perform.")
    description: str = Field(description="A short (3-5 word) description shown in the UI.")
    subagent_type: str = Field(
        default="general-purpose",
        description=(
            "Agent type to use. Built-in: scout, researcher, planner, worker, reviewer, oracle, "
            "general-purpose. Custom types from .tau/subagents/agents/*.md are also available."
        ),
    )
    model: str | None = Field(
        default=None,
        description='Optional model override (e.g. "claude-haiku-4-5"). Omit to use the type\'s default.',
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum agentic turns before the agent wraps up. Omit for unlimited.",
        gt=0,
    )
    run_in_background: bool = Field(
        default=False,
        description=(
            "Run in background — returns agent_id immediately. "
            "Call get_subagent_result to retrieve output when done."
        ),
    )
    inherit_context: bool = Field(
        default=False,
        description="Fork the current conversation into the agent so it starts with full context.",
    )
    resume: str | None = Field(
        default=None,
        description="Resume a previous agent by its agent_id.",
    )
    isolated: bool = Field(
        default=False,
        description="Only give the agent read-only tools (read, grep, glob, ls) — no write or terminal.",
    )


class GetSubagentResultInput(BaseModel):
    agent_id: str = Field(description="The agent_id returned by Agent.")
    wait: bool = Field(
        default=False,
        description="Block until the agent finishes before returning.",
    )
    verbose: bool = Field(
        default=False,
        description="Include per-turn summaries alongside the final result.",
    )


class SteerSubagentInput(BaseModel):
    agent_id: str = Field(description="The agent_id of the running agent to steer.")
    message: str = Field(
        description="Message injected as the next user turn — delivered after the current action completes.",
    )


# ── Tools ──────────────────────────────────────────────────────────────────────


class AgentTool(Tool):
    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager
        super().__init__(
            name="Agent",
            description=self._build_description(),
            schema=AgentInput,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Parallel,
            render_call=self._render_call,
            render_result=self._render_result,
        )

    def _build_description(self) -> str:
        types = self._manager.get_agent_types()
        type_lines = "\n".join(
            f"  - {t.display_name} ({t.name}): {t.description}"
            for t in types.values()
            if t.enabled
        )
        return (
            "Launch a sub-agent to handle a delegated task in an isolated session.\n\n"
            "Available agent types:\n"
            f"{type_lines}\n\n"
            "Use run_in_background=true for parallel execution. "
            "Use inherit_context=true to fork the current conversation into the agent. "
            "Use resume=agent_id to continue a previous agent session."
        )

    @staticmethod
    def _render_call(params: dict, _expanded: bool) -> list[str]:
        atype = params.get("subagent_type", "general-purpose")
        desc = params.get("description", "")
        bg = " [background]" if params.get("run_in_background") else ""
        fork = " [fork]" if params.get("inherit_context") else ""
        return [f"Agent({atype}){bg}{fork}  {desc}"]

    @staticmethod
    def _render_result(content: str, opts: Any) -> list[str]:
        lines = content.splitlines()
        if opts.is_partial:
            return lines[:3] + (["…"] if len(lines) > 3 else [])
        return lines

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        from .types import AgentStatus

        p = invocation.params
        llm = context.llm if context else None

        if llm is not None:
            self._manager.bind_llm(llm)

        self._manager.refresh_agent_types()

        try:
            record = await self._manager.spawn(
                prompt=p["prompt"],
                description=p.get("description", ""),
                subagent_type=p.get("subagent_type", "general-purpose"),
                model=p.get("model"),
                max_turns=p.get("max_turns"),
                run_in_background=bool(p.get("run_in_background", False)),
                inherit_context=bool(p.get("inherit_context", False)),
                resume=p.get("resume"),
                isolated=bool(p.get("isolated", False)),
                llm=llm,
            )
        except Exception as exc:
            return ToolResult.error(invocation.id, str(exc))

        if p.get("run_in_background"):
            return ToolResult.ok(
                invocation.id,
                (
                    f'<subagent id="{record.id}" status="queued">\n'
                    f"Agent '{record.agent_type}' spawned in background.\n"
                    f'Call get_subagent_result(agent_id="{record.id}") to retrieve output.\n'
                    f'Call steer_subagent(agent_id="{record.id}", message="...") to redirect it.\n'
                    "</subagent>"
                ),
            )

        if record.status == AgentStatus.ERROR:
            return ToolResult.error(
                invocation.id,
                f'<subagent id="{record.id}" status="error">\n{record.error}\n</subagent>',
            )

        result_text = record.result or "(no output)"
        return ToolResult.ok(
            invocation.id,
            (
                f'<subagent id="{record.id}" status="{record.status}" '
                f'turns="{record.turn_count}" tool_uses="{record.tool_uses}">\n'
                f"{result_text}\n"
                "</subagent>"
            ),
        )


class GetSubagentResultTool(Tool):
    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager
        super().__init__(
            name="get_subagent_result",
            description=(
                "Check the status of a background sub-agent and retrieve its result when done.\n"
                "Use wait=true to block until the agent completes."
            ),
            schema=GetSubagentResultInput,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_call=lambda p, _: [f"get_subagent_result({p.get('agent_id', '')})"],
            render_result=lambda c, _: c.splitlines(),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        from .types import AgentStatus

        agent_id = invocation.params.get("agent_id", "")
        wait = bool(invocation.params.get("wait", False))

        record = self._manager.get_record(agent_id)
        if record is None:
            return ToolResult.error(invocation.id, f"No agent with id {agent_id!r}")

        if wait and record.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
            while record.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
                if signal is not None and signal.is_set():
                    return ToolResult.error(invocation.id, "Cancelled while waiting.")
                await asyncio.sleep(0.5)

        result = self._manager.get_result(agent_id)
        lines = [
            f'<subagent id="{agent_id}" status="{result["status"]}">',
            f"turns={result['turn_count']}  tool_uses={result['tool_uses']}  "
            f"tokens={result['token_input'] + result['token_output']}",
        ]
        if result.get("error"):
            lines.append(f"error: {result['error']}")
        if result.get("result"):
            lines.append("")
            lines.append(result["result"])
        lines.append("</subagent>")

        return ToolResult.ok(invocation.id, "\n".join(lines))


class SubagentTool(Tool):
    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager
        super().__init__(
            name="steer_subagent",
            description=(
                "Send a mid-run steering message to redirect a running background agent. "
                "The message is injected as the next user turn after the current action completes."
            ),
            schema=SteerSubagentInput,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
            render_call=lambda p, _: [f"steer_subagent({p.get('agent_id', '')})"],
            render_result=lambda c, _: c.splitlines(),
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        agent_id = invocation.params.get("agent_id", "")
        message = invocation.params.get("message", "")

        ok = self._manager.steer(agent_id, message)
        if ok:
            return ToolResult.ok(
                invocation.id,
                f"Steering message queued for agent {agent_id}. "
                "It will be delivered after the current action completes.",
            )

        record = self._manager.get_record(agent_id)
        if record is None:
            return ToolResult.error(invocation.id, f"No agent with id {agent_id!r}")
        return ToolResult.error(
            invocation.id,
            f"Agent {agent_id} is not running (status={record.status}). Cannot steer.",
        )
