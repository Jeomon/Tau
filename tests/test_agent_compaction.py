"""Regression tests for agent compaction settings and circuit breaking."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tau.agent.service import Agent
from tau.agent.types import AgentPhase
from tau.hooks.engine import BeforeCompactionResult
from tau.hooks.service import Hooks
from tau.session.compaction import CompactionResult, CompactionSettings


class _Settings:
    def __init__(
        self,
        *,
        enabled: bool = True,
        reserve_tokens: int = 2_000,
        keep_recent_tokens: int = 4_000,
    ) -> None:
        self.enabled = enabled
        self.reserve_tokens = reserve_tokens
        self.keep_recent_tokens = keep_recent_tokens

    def is_compaction_enabled(self) -> bool:
        return self.enabled

    def get_compaction_reserve_tokens(self) -> int:
        return self.reserve_tokens

    def get_compaction_keep_recent_tokens(self) -> int:
        return self.keep_recent_tokens


def _agent(settings: _Settings | None, context_window: int = 10_000) -> Any:
    agent: Any = Agent.__new__(Agent)
    agent._engine = SimpleNamespace(_settings=settings)
    agent._config = SimpleNamespace(
        compaction=CompactionSettings(
            enabled=True,
            reserve_tokens=1_000,
            keep_recent_tokens=3_000,
        )
    )
    agent._context_window = context_window
    agent._compaction_failures = 0
    agent._compaction_circuit_notified = False
    return agent


def test_current_compaction_settings_uses_live_settings() -> None:
    settings = _Settings(enabled=False, reserve_tokens=2_500, keep_recent_tokens=5_000)
    agent = _agent(settings)

    resolved = agent._current_compaction_settings()

    assert resolved.enabled is False
    assert resolved.reserve_tokens == 2_500
    assert resolved.keep_recent_tokens == 5_000


def test_current_compaction_settings_clamps_invalid_live_budgets() -> None:
    agent = _agent(_Settings(reserve_tokens=9_000, keep_recent_tokens=9_000))

    resolved = agent._current_compaction_settings()

    assert resolved.reserve_tokens + resolved.keep_recent_tokens < agent._context_window


def test_circuit_breaker_notifies_once(monkeypatch) -> None:
    agent = _agent(_Settings())
    notifications: list[str] = []
    monkeypatch.setattr(agent, "_notify", notifications.append)

    for _ in range(4):
        try:
            raise RuntimeError("compaction failed")
        except RuntimeError:
            agent._record_compaction_failure("failed")

    assert agent._compaction_failures == 4
    assert len(notifications) == 1
    assert "disabled after 3 failures" in notifications[0]


def test_compaction_events_run_in_explicit_phase() -> None:
    agent = _agent(_Settings())
    agent._phase = AgentPhase.IDLE
    agent._runtime = None
    agent.hooks = Hooks()
    agent._session_manager = SimpleNamespace(append_compaction=lambda **kwargs: "entry")
    agent._run_compaction = AsyncMock(
        return_value=(
            CompactionResult(summary="summary", first_kept_entry_id="kept", tokens_before=100),
            False,
        )
    )
    observed: list[tuple[str, AgentPhase]] = []

    async def observe(event) -> None:
        if event.type.startswith("compaction_"):
            observed.append((event.type, agent._phase))

    agent.hooks.subscribe(observe)

    asyncio.run(agent._apply_compaction(SimpleNamespace(), [], manual=True))

    assert observed == [("compaction_end", AgentPhase.COMPACTION)]
    assert agent._phase == AgentPhase.IDLE


def test_cancelled_compaction_emits_cancel_event_and_restores_phase() -> None:
    agent = _agent(_Settings())
    agent._phase = AgentPhase.IDLE
    agent._runtime = None
    agent.hooks = Hooks()
    agent._session_manager = SimpleNamespace()
    events: list[str] = []
    agent.hooks.register(
        "before_compaction",
        lambda event: BeforeCompactionResult(cancel=True),
    )
    agent.hooks.subscribe(lambda event: events.append(event.type))

    with pytest.raises(RuntimeError, match="cancelled by extension"):
        asyncio.run(agent._apply_compaction(SimpleNamespace(), [], manual=True))

    assert "compaction_cancelled" in events
    assert "compaction_failure" not in events
    assert agent._phase == AgentPhase.IDLE


def test_wait_for_idle_includes_post_run_processing() -> None:
    async def scenario() -> None:
        agent: Any = Agent.__new__(Agent)
        agent._phase = AgentPhase.IDLE
        agent._idle_event = asyncio.Event()
        agent._idle_event.set()
        agent._signal = asyncio.Event()
        agent._overflow_recovery_attempted = False
        agent._session_manager = SimpleNamespace(append_message=lambda *args, **kwargs: "entry")
        agent._engine = SimpleNamespace(
            llm=SimpleNamespace(api=SimpleNamespace(options=SimpleNamespace(signal=None))),
            has_pending_messages=lambda: False,
        )
        agent.hooks = Hooks()
        agent._build_turn_context = lambda: SimpleNamespace()
        agent._run = AsyncMock()
        post_run_started = asyncio.Event()
        release_post_run = asyncio.Event()

        async def check_compaction() -> None:
            post_run_started.set()
            await release_post_run.wait()

        agent._check_compaction = check_compaction

        invoke_task = asyncio.create_task(agent.invoke("hello"))
        await post_run_started.wait()
        wait_task = asyncio.create_task(agent.wait_for_idle())
        await asyncio.sleep(0)

        assert agent.phase is AgentPhase.TURN
        assert not wait_task.done()

        release_post_run.set()
        await invoke_task
        await wait_task

        assert agent.phase is AgentPhase.IDLE

    asyncio.run(scenario())


def test_public_live_state_is_exposed_as_snapshots() -> None:
    streaming = SimpleNamespace()
    steering = SimpleNamespace(snapshot=lambda: ["steer"])
    followup = SimpleNamespace(snapshot=lambda: ["followup"])
    agent: Any = Agent.__new__(Agent)
    agent._engine = SimpleNamespace(
        state=SimpleNamespace(
            streaming_message=streaming,
            pending_tool_calls={"a", "b"},
            error_message="failed",
            steering_queue=steering,
            follow_up_queue=followup,
        )
    )

    assert agent.streaming_message is streaming
    assert agent.pending_tool_call_ids == frozenset({"a", "b"})
    assert agent.error_message == "failed"
    assert agent.queued_messages == {
        "steering": ["steer"],
        "followup": ["followup"],
    }
