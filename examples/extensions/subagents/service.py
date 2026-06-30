from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .types import AgentRecord, AgentStatus, AgentTypeDef

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM

_log = logging.getLogger(__name__)

_TOOL_NAME_MAP = {
    "read": "read",
    "write": "write",
    "edit": "edit",
    "terminal": "terminal",
    "bash": "terminal",
    "glob": "glob",
    "grep": "grep",
    "ls": "ls",
}


def _select_tools(tool_spec: list[str] | str, disallowed_tools: list[str] | None = None) -> list:
    from tau.builtins.tools import TOOLS

    denied = {_TOOL_NAME_MAP.get(name, name) for name in (disallowed_tools or [])}
    if tool_spec == "all" or tool_spec == "*":
        selected = list(TOOLS)
    elif tool_spec == "none" or tool_spec == "":
        selected = []
    elif isinstance(tool_spec, list):
        allowed = {_TOOL_NAME_MAP.get(t, t) for t in tool_spec}
        selected = [t for t in TOOLS if t.name in allowed]
    else:
        selected = list(TOOLS)
    return [tool for tool in selected if tool.name not in denied]


def _count_tool_uses(session_manager) -> int:
    from tau.message.types import ToolMessage
    from tau.session.types import MessageEntry

    return sum(
        1
        for entry in session_manager.get_branch()
        if isinstance(entry, MessageEntry) and isinstance(entry.message, ToolMessage)
    )


def _collect_output(session_manager) -> str:
    from tau.message.types import AssistantMessage, TextContent
    from tau.session.types import MessageEntry

    parts: list[str] = []
    for entry in session_manager.get_branch():
        if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
            for content in entry.message.contents:
                if isinstance(content, TextContent) and content.content.strip():
                    parts.append(content.content)
    return "\n\n".join(parts)


class Subagent:
    def __init__(
        self,
        output_dir: Path,
        cwd: Path,
        grace_turns: int = 1,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._cwd = cwd
        self._grace_turns = max(1, grace_turns)
        self._on_update = on_update

    async def run(
        self,
        record: AgentRecord,
        llm: TextLLM,
        agent_type: AgentTypeDef,
        context_messages: list | None = None,
        run_cwd: Path | None = None,
    ) -> None:
        from tau.agent.service import Agent
        from tau.agent.types import AgentConfig
        from tau.engine.service import Engine
        from tau.engine.types import ToolExecutionStartEvent
        from tau.hooks.service import Hooks
        from tau.session.manager import SessionManager

        from .memory import build_memory_block

        record.status = AgentStatus.RUNNING
        record.started_at = asyncio.get_event_loop().time()

        if record.output_file is None:
            out_dir = self._output_dir / record.id
            out_dir.mkdir(parents=True, exist_ok=True)
            record.output_file = out_dir / "session.jsonl"
        session_file = record.output_file

        from .skills import build_skills_block

        tool_spec = ["read", "grep", "glob", "ls"] if record.isolated else agent_type.tools
        tools = _select_tools(tool_spec, agent_type.disallowed_tools)
        effective_cwd = run_cwd or self._cwd
        hooks = Hooks()
        engine = Engine(cwd=effective_cwd, llm=llm, tools=tools)

        async def on_engine_event(event) -> None:
            if isinstance(event, ToolExecutionStartEvent):
                record.tool_uses += 1
            if self._on_update is not None:
                self._on_update()

        await engine.subscribe(on_engine_event)
        system_prompt = agent_type.system_prompt
        skills_block = build_skills_block(agent_type.skills, self._cwd)
        if skills_block:
            system_prompt = "\n\n".join(part for part in (system_prompt, skills_block) if part)
        if agent_type.memory:
            writable = any(tool.name in {"write", "edit", "terminal"} for tool in tools)
            memory_block = build_memory_block(
                agent_name=agent_type.name,
                scope=agent_type.memory,
                cwd=self._cwd,
                writable=writable,
            )
            system_prompt = "\n\n".join(part for part in (system_prompt, memory_block) if part)

        session_manager = SessionManager(
            cwd=effective_cwd,
            session_file=session_file,
            persist=True,
        )

        config = AgentConfig(cwd=effective_cwd, system_prompt=system_prompt)
        agent = Agent(engine=engine, session_manager=session_manager, config=config, hooks=hooks)

        # Seed with parent context for fork
        if context_messages:
            for msg in context_messages:
                session_manager.append_message(msg)

        try:
            max_turns = record.max_turns or agent_type.max_turns
            turn_limit_hit = False

            # First invoke
            await agent.invoke(record.prompt)
            record.turn_count += 1

            # Steering loop — process queued messages as follow-up turns
            while not record.stop_event.is_set():
                if max_turns and record.turn_count >= max_turns:
                    turn_limit_hit = True
                    for grace_turn in range(self._grace_turns):
                        remaining = self._grace_turns - grace_turn
                        await agent.invoke(
                            "The task turn limit has been reached. "
                            f"You have {remaining} wrap-up turn(s) remaining. "
                            "Stop new work and provide the final summary now."
                        )
                        record.turn_count += 1
                        if record.stop_event.is_set():
                            break
                    break

                if record.steering_queue:
                    msg = record.steering_queue.pop(0)
                    await agent.invoke(msg)
                    record.turn_count += 1
                else:
                    break

            # Collect usage from LLM (best-effort)
            try:
                usage = llm.api.last_usage  # may not exist on all backends
                if usage:
                    record.token_input = getattr(usage, "input_tokens", 0)
                    record.token_output = getattr(usage, "output_tokens", 0)
            except AttributeError:
                pass

            record.result = _collect_output(session_manager)
            record.tool_uses = _count_tool_uses(session_manager)
            record.status = AgentStatus.STEERED if turn_limit_hit else AgentStatus.COMPLETED

        except asyncio.CancelledError:
            record.status = AgentStatus.STOPPED
            record.result = _collect_output(session_manager)
            raise
        except Exception as exc:
            _log.exception("Subagent %s failed", record.id)
            record.status = AgentStatus.ERROR
            record.error = str(exc)
        finally:
            record.finished_at = asyncio.get_event_loop().time()
