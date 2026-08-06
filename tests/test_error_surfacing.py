"""Covers how an unrecoverable failure reaches the user.

Three separate paths, each previously silent in its own way:

1. Engine catch-all (``tau/engine/service.py``) — a failure raised outside
   the provider stream (client construction, a bug in the loop) only emitted
   ``agent_error``. No message carried it, so it reached the TUI through no
   normal path and left nothing in the session to show after a resume. It
   now ends a message with ``stop_reason=Error`` the way the in-stream
   error branch does.
2. ``Agent.invoke`` (``tau/agent/service.py``) — re-raised before reaching
   the ``SettledEvent`` emit, so anything keyed on ``settled`` waited on an
   event that never arrived for the rest of the session.
3. JSON mode (``tau/console/cli.py``) — did not subscribe to ``agent_error``,
   so a consumer of the event stream never saw the failure named.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tau.agent.service import Agent, AgentPhase
from tau.console.cli import _run_json
from tau.engine.service import Engine
from tau.engine.types import EngineContext
from tau.hooks.engine import AgentErrorEvent, SettledEvent
from tau.hooks.service import Hooks
from tau.inference.types import EndEvent, StopReason, TextEndEvent
from tau.message.types import AssistantMessage, TextContent, UserMessage

# ── Engine: the catch-all path now carries the error on a message ─────────────


class _Model:
    name = "fake-model"


class _RaisingLLM:
    """A model whose stream blows up instead of yielding events.

    ``before`` events are yielded first, so a test can choose whether the
    failure lands mid-message or before anything streamed.
    """

    def __init__(self, exc: Exception, before: list | None = None) -> None:
        self.model = _Model()
        self.api = SimpleNamespace(options=SimpleNamespace(headers={}, on_response=None))
        self.provider_id = "fake-provider"
        self._exc = exc
        self._before = before or []

    def stream(self, _ctx):
        return self._gen()

    async def _gen(self):
        for event in self._before:
            yield event
        raise self._exc


def _run_engine(llm) -> list:
    engine = Engine(cwd=Path("."), llm=llm, tools=[], system_prompt="")  # type: ignore[arg-type]
    events: list = []
    engine.hooks.subscribe(lambda e: events.append(e))
    asyncio.run(engine.run(EngineContext(system_prompt="", messages=[UserMessage.from_text("hi")])))
    return events


def _message_ends(events: list) -> list:
    return [e for e in events if getattr(e, "type", None) == "message_end"]


def test_pre_stream_failure_ends_a_message_carrying_the_error() -> None:
    """The reported case: the client cannot even be constructed."""
    events = _run_engine(_RaisingLLM(ValueError("requires a project ID")))

    ends = _message_ends(events)
    assert len(ends) == 1
    msg = ends[0].message
    assert isinstance(msg, AssistantMessage)
    assert msg.stop_reason == StopReason.Error
    assert "requires a project ID" in (msg.error or "")


def test_catch_all_still_emits_agent_error() -> None:
    """The message is additive — the existing event contract is unchanged."""
    events = _run_engine(_RaisingLLM(ValueError("boom")))

    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(errors) == 1
    assert "boom" in errors[0].error


def test_mid_stream_failure_adopts_the_in_flight_message() -> None:
    """A partial response must not be left dangling beside a second marker."""
    events = _run_engine(
        _RaisingLLM(
            RuntimeError("connection reset"),
            before=[TextEndEvent(text=TextContent(content="partial answer"))],
        )
    )

    ends = _message_ends(events)
    assert len(ends) == 1
    msg = ends[0].message
    assert msg.stop_reason == StopReason.Error
    assert "connection reset" in (msg.error or "")
    # The text streamed before the failure survives on that same message.
    text = "".join(c.content for c in msg.contents if isinstance(c, TextContent))
    assert text == "partial answer"


def test_successful_turn_is_unaffected() -> None:
    class _OkLLM(_RaisingLLM):
        async def _gen(self):
            yield TextEndEvent(text=TextContent(content="all good"))
            yield EndEvent(reason=StopReason.Stop)

    events = _run_engine(_OkLLM(ValueError("never raised")))

    assert not [e for e in events if isinstance(e, AgentErrorEvent)]
    ends = _message_ends(events)
    assert len(ends) == 1
    assert ends[0].message.stop_reason == StopReason.Stop


# ── Agent: a failed turn still settles ───────────────────────────────────────


def _make_agent(run_side_effect: Any = None) -> Any:
    agent: Any = Agent.__new__(Agent)
    agent._phase = AgentPhase.IDLE
    agent._idle_event = asyncio.Event()
    agent._idle_event.set()
    agent._signal = asyncio.Event()
    agent._abort_requested = False
    agent._overflow_recovery_attempted = False
    agent._session_manager = SimpleNamespace(append_message=lambda *a, **kw: "entry")
    agent._engine = SimpleNamespace(
        llm=SimpleNamespace(api=SimpleNamespace(options=SimpleNamespace(signal=None))),
        has_pending_messages=lambda: False,
    )
    agent.hooks = Hooks()
    agent._build_turn_context = lambda: SimpleNamespace()
    agent._run = AsyncMock(side_effect=run_side_effect)
    agent._run_continue = AsyncMock()
    agent._check_compaction = AsyncMock(return_value=False)
    agent._try_overflow_recovery = AsyncMock(return_value=False)
    return agent


def _settled_counter(agent: Any) -> list:
    seen: list = []
    agent.hooks.register("settled", lambda event: seen.append(event))
    return seen


@pytest.mark.asyncio
async def test_failed_turn_still_emits_settled() -> None:
    agent = _make_agent(run_side_effect=RuntimeError("Agent failed: boom."))
    seen = _settled_counter(agent)

    with pytest.raises(RuntimeError):
        await agent.invoke("hello")

    assert len(seen) == 1
    assert isinstance(seen[0], SettledEvent)


@pytest.mark.asyncio
async def test_failed_turn_is_idle_when_settled_fires() -> None:
    """Handlers gate on is_idle() to decide whether they may start work."""
    agent = _make_agent(run_side_effect=RuntimeError("boom"))
    phases: list = []
    agent.hooks.register("settled", lambda _e: phases.append(agent.is_idle()))

    with pytest.raises(RuntimeError):
        await agent.invoke("hello")

    assert phases == [True]


@pytest.mark.asyncio
async def test_successful_turn_settles_exactly_once() -> None:
    agent = _make_agent()
    seen = _settled_counter(agent)

    await agent.invoke("hello")

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_emit_settled() -> None:
    """CancelledError unwinds the task; awaiting hooks there is not reliable."""
    agent = _make_agent(run_side_effect=asyncio.CancelledError())
    seen = _settled_counter(agent)

    with pytest.raises(asyncio.CancelledError):
        await agent.invoke("hello")

    assert seen == []


# ── JSON mode: agent_error reaches the event stream ──────────────────────────


class _ErroringRuntime:
    def __init__(self) -> None:
        self.hooks = Hooks()

    async def invoke(self, _message: str) -> None:
        await self.hooks.emit(AgentErrorEvent(error="requires a project ID"))
        await self.hooks.emit(SettledEvent())


def test_json_mode_streams_agent_error(capsys) -> None:
    asyncio.run(_run_json(_ErroringRuntime(), "prompt"))  # type: ignore[arg-type]
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]

    errors = [e for e in events if e["type"] == "agent_error"]
    assert len(errors) == 1
    assert errors[0]["error"] == "requires a project ID"
