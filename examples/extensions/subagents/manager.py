from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import Callable
from fnmatch import fnmatch
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
        scope_models: bool = False,
        default_max_turns: int | None = None,
    ) -> None:
        self._cwd = cwd
        self._output_dir = output_dir
        self._max_concurrent = max_concurrent
        self._grace_turns = grace_turns
        self._disable_builtins = disable_builtins
        self._scope_models = scope_models
        self._default_max_turns = default_max_turns
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._records: dict[str, AgentRecord] = {}
        self._llm: TextLLM | None = None
        self._agent_types: dict[str, AgentTypeDef] = {}
        self._update_callback: Callable[[], None] | None = None
        self._runner = Subagent(
            output_dir=output_dir,
            cwd=cwd,
            grace_turns=grace_turns,
            on_update=self._notify_update,
        )
        self._listeners: list[Callable[[str, dict[str, Any]], Any]] = []
        self._scheduler: Any | None = None
        self._parent_session_manager: Any | None = None
        self._restore_records()

    def set_update_callback(self, callback: Callable[[], None] | None) -> None:
        self._update_callback = callback

    def _notify_update(self) -> None:
        if self._update_callback is not None:
            self._update_callback()

    def subscribe(self, callback: Callable[[str, dict[str, Any]], Any]) -> Callable[[], None]:
        """Subscribe to subagent lifecycle events and return an unsubscribe callback."""
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    async def _emit(self, event_type: str, record: AgentRecord) -> None:
        payload = record.to_status_dict()
        payload["result"] = record.result
        payload["error"] = record.error
        for listener in list(self._listeners):
            try:
                result = listener(event_type, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _log.exception("Subagent lifecycle listener failed for %s", event_type)

    def bind_llm(self, llm: TextLLM) -> None:
        self._llm = llm

    def bind_parent_session(self, session_manager: Any | None) -> None:
        self._parent_session_manager = session_manager

    def refresh_agent_types(self) -> None:
        self._agent_types = load_agent_types(self._cwd, disable_builtins=self._disable_builtins)

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
        isolation: str | None = None,
        llm: TextLLM | None = None,
        parent_session_manager: Any = None,
        enabled_models: list[str] | None = None,
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
        effective_model = model or agent_type.model
        effective_isolation = isolation or agent_type.isolation
        if effective_model and self._scope_models and enabled_models:
            model_id = effective_model.split("/", 1)[-1]
            if not any(
                fnmatch(effective_model, pattern) or fnmatch(model_id, pattern)
                for pattern in enabled_models
            ):
                raise ValueError(f"Subagent model {effective_model!r} is outside enabled_models.")
        if effective_model:
            current_ref = f"{effective_llm.provider_id}/{effective_llm.model.id}"
            if effective_model not in {effective_llm.model.id, current_ref}:
                from tau.inference.api.text.service import TextLLM

                if "/" in effective_model:
                    provider, model_id = effective_model.split("/", 1)
                else:
                    provider, model_id = None, effective_model
                effective_llm = TextLLM(model_id=model_id, provider=provider)

        record = AgentRecord(
            id=str(uuid.uuid4())[:8],
            agent_type=agent_type.name,
            description=description,
            prompt=prompt,
            status=AgentStatus.QUEUED,
            model=effective_model,
            max_turns=max_turns or agent_type.max_turns or self._default_max_turns,
            run_in_background=run_in_background,
            isolated=isolated,
            isolation=effective_isolation,
        )
        record.output_file = self._output_dir / record.id / "session.jsonl"
        self._records[record.id] = record
        self._persist_record(record)
        self._notify_update()
        await self._emit("created", record)

        context_messages = None
        parent_session = parent_session_manager or self._parent_session_manager
        if inherit_context and parent_session:
            context_messages = self._extract_context(parent_session)

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
        worktree = None
        try:
            if record.isolation == "worktree":
                from .worktree import create_worktree

                worktree = await create_worktree(self._cwd, record.id)
                record.worktree_path = worktree.path
                record.branch = worktree.branch
                self._persist_record(record)
            async with self._semaphore:
                record.status = AgentStatus.RUNNING
                self._notify_update()
                await self._emit("started", record)
                await self._runner.run(
                    record=record,
                    llm=llm,
                    agent_type=agent_type,
                    context_messages=context_messages,
                    run_cwd=worktree.cwd if worktree is not None else None,
                )
        except Exception as exc:
            record.status = AgentStatus.ERROR
            record.error = str(exc)
            _log.exception("Isolated subagent setup or execution failed")
        finally:
            if worktree is not None:
                from .worktree import finalize_worktree

                changed, error = await finalize_worktree(worktree, record.description)
                if error:
                    record.error = f"{record.error + '; ' if record.error else ''}{error}"
                    record.status = AgentStatus.ERROR
                elif changed and record.result:
                    record.result += f"\n\nChanges committed to branch `{worktree.branch}`."
            self._persist_record(record)
            self._notify_update()
            event_type = (
                "completed"
                if record.status in (AgentStatus.COMPLETED, AgentStatus.STEERED)
                else "failed"
            )
            await self._emit(event_type, record)

    async def _resume(self, agent_id: str, prompt: str) -> AgentRecord:
        record = self._records.get(agent_id)
        if record is None:
            raise ValueError(f"No agent with id {agent_id!r}")
        if record.status not in (
            AgentStatus.COMPLETED,
            AgentStatus.STEERED,
            AgentStatus.STOPPED,
            AgentStatus.ABORTED,
            AgentStatus.ERROR,
        ):
            raise RuntimeError(
                f"Agent {agent_id} is not in a terminal state (status={record.status})"
            )

        # Create a continuation record reusing the output session
        agent_type = self.get_agent_type(record.agent_type)
        if agent_type is None:
            raise ValueError(f"Agent type {record.agent_type!r} no longer available")

        if self._llm is None:
            raise RuntimeError("No LLM bound.")
        if record.output_file is None or not record.output_file.is_file():
            raise RuntimeError(f"Agent {agent_id} has no persisted session to resume.")

        resume_record = AgentRecord(
            id=str(uuid.uuid4())[:8],
            agent_type=record.agent_type,
            description=f"resume:{agent_id}",
            prompt=prompt,
            status=AgentStatus.QUEUED,
            model=record.model,
            max_turns=record.max_turns,
            run_in_background=record.run_in_background,
            isolated=record.isolated,
            isolation=record.isolation,
            output_file=record.output_file,
        )
        self._records[resume_record.id] = resume_record
        self._persist_record(resume_record)
        self._notify_update()

        if record.run_in_background:
            task = asyncio.create_task(self._run_queued(resume_record, self._llm, agent_type, None))
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
        self._notify_update()
        asyncio.create_task(self._emit("steered", record))
        return True

    def stop(self, agent_id: str) -> bool:
        record = self._records.get(agent_id)
        if record is None:
            return False
        record.stop_event.set()
        if record.task and not record.task.done():
            record.task.cancel()
        record.status = AgentStatus.STOPPED
        self._persist_record(record)
        self._notify_update()
        return True

    async def rpc_spawn(self, request: dict[str, Any]) -> dict[str, Any]:
        """Stable cross-extension spawn boundary returning a serializable envelope."""
        try:
            record = await self.spawn(
                prompt=str(request["prompt"]),
                description=str(request.get("description", "")),
                subagent_type=str(request.get("subagent_type", "general-purpose")),
                model=request.get("model"),
                max_turns=request.get("max_turns"),
                run_in_background=bool(request.get("run_in_background", True)),
                inherit_context=bool(request.get("inherit_context", False)),
                resume=request.get("resume"),
                isolated=bool(request.get("isolated", False)),
                isolation=request.get("isolation"),
                enabled_models=request.get("_enabled_models"),
            )
        except Exception as exc:
            return {"success": False, "data": None, "error": str(exc)}
        return {"success": True, "data": record.to_status_dict(), "error": None}

    def rpc_stop(self, agent_id: str) -> dict[str, Any]:
        stopped = self.stop(agent_id)
        return {
            "success": stopped,
            "data": {"agent_id": agent_id} if stopped else None,
            "error": None if stopped else f"No agent with id {agent_id!r}",
        }

    @staticmethod
    def rpc_ping() -> dict[str, Any]:
        return {"success": True, "data": {"protocol_version": 1}, "error": None}

    def set_scheduler(self, scheduler: Any | None) -> None:
        self._scheduler = scheduler

    def schedule(self, expression: str, request: dict[str, Any]) -> Any:
        if self._scheduler is None:
            raise RuntimeError("Subagent scheduler is not available.")
        return self._scheduler.add(expression, request)

    def list_schedules(self) -> list[Any]:
        return self._scheduler.list_jobs() if self._scheduler is not None else []

    def cancel_schedule(self, job_id: str) -> bool:
        return self._scheduler.cancel(job_id) if self._scheduler is not None else False

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

    def _persist_record(self, record: AgentRecord) -> None:
        record_dir = self._output_dir / record.id
        record_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": record.id,
            "agent_type": record.agent_type,
            "description": record.description,
            "prompt": record.prompt,
            "status": record.status.value,
            "model": record.model,
            "max_turns": record.max_turns,
            "run_in_background": record.run_in_background,
            "isolated": record.isolated,
            "isolation": record.isolation,
            "worktree_path": str(record.worktree_path) if record.worktree_path else None,
            "branch": record.branch,
            "result": record.result,
            "error": record.error,
            "token_input": record.token_input,
            "token_output": record.token_output,
            "tool_uses": record.tool_uses,
            "turn_count": record.turn_count,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "output_file": str(record.output_file) if record.output_file else None,
        }
        target = record_dir / "record.json"
        temporary = record_dir / ".record.json.tmp"
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _restore_records(self) -> None:
        if not self._output_dir.is_dir():
            return
        for path in sorted(self._output_dir.glob("*/record.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                status = AgentStatus(data["status"])
                if status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
                    status = AgentStatus.STOPPED
                output_raw = data.get("output_file")
                record = AgentRecord(
                    id=data["id"],
                    agent_type=data["agent_type"],
                    description=data.get("description", ""),
                    prompt=data.get("prompt", ""),
                    status=status,
                    model=data.get("model"),
                    max_turns=data.get("max_turns"),
                    run_in_background=bool(data.get("run_in_background", False)),
                    isolated=bool(data.get("isolated", False)),
                    isolation=data.get("isolation"),
                    worktree_path=(
                        Path(data["worktree_path"]) if data.get("worktree_path") else None
                    ),
                    branch=data.get("branch"),
                    result=data.get("result"),
                    error=data.get("error"),
                    token_input=int(data.get("token_input", 0)),
                    token_output=int(data.get("token_output", 0)),
                    tool_uses=int(data.get("tool_uses", 0)),
                    turn_count=int(data.get("turn_count", 0)),
                    created_at=float(data.get("created_at", 0)),
                    started_at=data.get("started_at"),
                    finished_at=data.get("finished_at"),
                    output_file=Path(output_raw) if output_raw else None,
                )
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                _log.warning("Ignoring invalid subagent record: %s", path)
                continue
            self._records[record.id] = record
