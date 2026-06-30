from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agents import AgentTypeDef, load_agent_types
from .service import Subagent
from .types import AgentRecord, AgentStatus

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM

_log = logging.getLogger(__name__)


class SubagentManager:
    def __init__(
        self,
        cwd: Path,
        output_dir: Path,
        max_concurrent: int = 4,
        grace_turns: int = 5,
        disable_builtins: bool = False,
    ) -> None:
        self._cwd = cwd
        self._output_dir = output_dir
        self._max_concurrent = max_concurrent
        self._grace_turns = grace_turns
        self._disable_builtins = disable_builtins
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._records: dict[str, AgentRecord] = {}
        self._runner = Subagent(output_dir=output_dir, cwd=cwd)
        self._llm: TextLLM | None = None
        self._agent_types: dict[str, AgentTypeDef] = {}

    def bind_llm(self, llm: TextLLM) -> None:
        self._llm = llm

    def refresh_agent_types(self) -> None:
        self._agent_types = load_agent_types(
            self._cwd, disable_builtins=self._disable_builtins
        )

    def get_agent_types(self) -> dict[str, AgentTypeDef]:
        if not self._agent_types:
            self.refresh_agent_types()
        return self._agent_types

    def get_agent_type(self, name: str) -> AgentTypeDef | None:
        types = self.get_agent_types()
        return types.get(name.lower()) or types.get("general-purpose")

    # ── Spawn ──────────────────────────────────────────────────────────────────

    async def spawn(
        self,
        *,
        prompt: str,
        description: str,
        subagent_type: str = "general-purpose",
        model: str | None = None,
        max_turns: int | None = None,
        run_in_background: bool = False,
        inherit_context: bool = False,
        resume: str | None = None,
        isolated: bool = False,
        llm: TextLLM | None = None,
        parent_session_manager: Any = None,
    ) -> AgentRecord:
        # Resume existing agent
        if resume:
            return await self._resume(resume, prompt)

        agent_type = self.get_agent_type(subagent_type) or self.get_agent_type("general-purpose")
        if agent_type is None:
            raise ValueError(f"Unknown agent type: {subagent_type!r}")

        effective_llm = llm or self._llm
        if effective_llm is None:
            raise RuntimeError("No LLM bound to SubagentManager yet.")

        record = AgentRecord(
            id=str(uuid.uuid4())[:8],
            agent_type=agent_type.name,
            description=description,
            prompt=prompt,
            status=AgentStatus.QUEUED,
            model=model or agent_type.model,
            max_turns=max_turns or agent_type.max_turns,
            run_in_background=run_in_background,
        )
        self._records[record.id] = record

        context_messages = None
        if inherit_context and parent_session_manager:
            context_messages = self._extract_context(parent_session_manager)

        if run_in_background:
            task = asyncio.create_task(
                self._run_queued(record, effective_llm, agent_type, context_messages)
            )
            record.task = task
        else:
            await self._run_queued(record, effective_llm, agent_type, context_messages)

        return record

    async def _run_queued(
        self,
        record: AgentRecord,
        llm: TextLLM,
        agent_type: AgentTypeDef,
        context_messages: list | None,
    ) -> None:
        async with self._semaphore:
            await self._runner.run(
                record=record,
                llm=llm,
                agent_type=agent_type,
                context_messages=context_messages,
            )

    async def _resume(self, agent_id: str, prompt: str) -> AgentRecord:
        record = self._records.get(agent_id)
        if record is None:
            raise ValueError(f"No agent with id {agent_id!r}")
        if record.status not in (AgentStatus.COMPLETED, AgentStatus.STEERED, AgentStatus.ERROR):
            raise RuntimeError(f"Agent {agent_id} is not in a terminal state (status={record.status})")

        # Create a continuation record reusing the output session
        agent_type = self.get_agent_type(record.agent_type)
        if agent_type is None:
            raise ValueError(f"Agent type {record.agent_type!r} no longer available")

        if self._llm is None:
            raise RuntimeError("No LLM bound.")

        resume_record = AgentRecord(
            id=str(uuid.uuid4())[:8],
            agent_type=record.agent_type,
            description=f"resume:{agent_id}",
            prompt=prompt,
            status=AgentStatus.QUEUED,
            model=record.model,
            max_turns=record.max_turns,
            run_in_background=record.run_in_background,
        )
        self._records[resume_record.id] = resume_record

        if record.run_in_background:
            task = asyncio.create_task(
                self._run_queued(resume_record, self._llm, agent_type, None)
            )
            resume_record.task = task
        else:
            await self._run_queued(resume_record, self._llm, agent_type, None)

        return resume_record

    # ── Control ────────────────────────────────────────────────────────────────

    def steer(self, agent_id: str, message: str) -> bool:
        record = self._records.get(agent_id)
        if record is None or record.status != AgentStatus.RUNNING:
            return False
        record.steering_queue.append(message)
        return True

    def stop(self, agent_id: str) -> bool:
        record = self._records.get(agent_id)
        if record is None:
            return False
        record.stop_event.set()
        if record.task and not record.task.done():
            record.task.cancel()
        record.status = AgentStatus.STOPPED
        return True

    # ── Query ──────────────────────────────────────────────────────────────────

    def get_record(self, agent_id: str) -> AgentRecord | None:
        return self._records.get(agent_id)

    def list_records(self) -> list[AgentRecord]:
        return list(self._records.values())

    def get_result(self, agent_id: str) -> dict:
        record = self._records.get(agent_id)
        if record is None:
            return {"error": f"No agent with id {agent_id!r}"}

        base = record.to_status_dict()
        if record.status in (
            AgentStatus.COMPLETED,
            AgentStatus.STEERED,
            AgentStatus.ERROR,
            AgentStatus.STOPPED,
            AgentStatus.ABORTED,
        ):
            base["result"] = record.result
            base["error"] = record.error
        return base

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _extract_context(self, session_manager: Any) -> list:
        from tau.message.types import AssistantMessage, UserMessage
        from tau.session.types import MessageEntry

        messages = []
        for entry in session_manager.get_branch():
            if isinstance(entry, MessageEntry) and isinstance(
                entry.message, (UserMessage, AssistantMessage)
            ):
                messages.append(entry.message)
        return messages

    def shutdown(self) -> None:
        for record in self._records.values():
            if record.status == AgentStatus.RUNNING:
                self.stop(record.id)
