from __future__ import annotations

import asyncio
import logging
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


def _select_tools(tool_spec: list[str] | str) -> list:
    from tau.builtins.tools import TOOLS

    if tool_spec == "all" or tool_spec == "*":
        return list(TOOLS)
    if tool_spec == "none" or tool_spec == "":
        return []
    if isinstance(tool_spec, list):
        allowed = {_TOOL_NAME_MAP.get(t, t) for t in tool_spec}
        return [t for t in TOOLS if t.name in allowed]
    return list(TOOLS)


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
    def __init__(self, output_dir: Path, cwd: Path) -> None:
        self._output_dir = output_dir
        self._cwd = cwd

    async def run(
        self,
        record: AgentRecord,
        llm: TextLLM,
        agent_type: AgentTypeDef,
        context_messages: list | None = None,
    ) -> None:
        from tau.agent.service import Agent
        from tau.agent.types import AgentConfig
        from tau.engine.service import Engine
        from tau.hooks.service import Hooks
        from tau.session.manager import SessionManager

        record.status = AgentStatus.RUNNING
        record.started_at = asyncio.get_event_loop().time()

        out_dir = self._output_dir / record.id
        out_dir.mkdir(parents=True, exist_ok=True)
        session_file = out_dir / "session.jsonl"
        record.output_file = session_file

        tools = _select_tools(agent_type.tools)
        hooks = Hooks()
        engine = Engine(cwd=self._cwd, llm=llm, tools=tools)
        system_prompt = agent_type.system_prompt

        session_manager = SessionManager(
            cwd=self._cwd,
            session_file=session_file,
            persist=True,
        )

        config = AgentConfig(cwd=self._cwd, system_prompt=system_prompt)
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
                    # Grace: ask agent to wrap up
                    await agent.invoke(
                        "Please wrap up your work immediately and provide a final summary."
                    )
                    record.turn_count += 1
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
