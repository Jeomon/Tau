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
from tau.message.types import UserMessage
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


def test_estimate_indicates_overflow_true_when_over_window() -> None:
    """Numeric guard: independent of error text, a request whose own estimate
    already reaches the context window should read as overflow — this is what
    catches a provider that rejects with unusual phrasing (see NVIDIA's
    "max_tokens must be at least 1, got -128", tau/inference/utils.py)."""
    agent = _agent(_Settings(), context_window=100)
    agent._session_manager = SimpleNamespace(
        build_session_context=lambda: SimpleNamespace(
            messages=[UserMessage.from_text("word " * 1000)]
        )
    )
    agent._system_prompt = ""
    agent._engine.tools = []

    assert agent._estimate_indicates_overflow() is True


def test_estimate_indicates_overflow_false_when_under_window() -> None:
    agent = _agent(_Settings(), context_window=100_000)
    agent._session_manager = SimpleNamespace(
        build_session_context=lambda: SimpleNamespace(messages=[UserMessage.from_text("hi")])
    )
    agent._system_prompt = ""
    agent._engine.tools = []

    assert agent._estimate_indicates_overflow() is False


def test_estimate_indicates_overflow_false_for_zero_context_window() -> None:
    agent = _agent(_Settings(), context_window=0)
    agent._session_manager = SimpleNamespace(
        build_session_context=lambda: SimpleNamespace(
            messages=[UserMessage.from_text("word " * 1000)]
        )
    )
    agent._system_prompt = ""
    agent._engine.tools = []

    assert agent._estimate_indicates_overflow() is False


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


def test_compaction_notify_only_for_manual(monkeypatch) -> None:
    from tau.extensions.context import ExtensionContext

    notifications: list[str] = []
    fake_ctx = SimpleNamespace(ui=SimpleNamespace(notify=notifications.append))
    monkeypatch.setattr(
        ExtensionContext, "from_runtime", classmethod(lambda cls, runtime: fake_ctx)
    )

    def run(manual: bool) -> None:
        agent = _agent(_Settings())
        agent._phase = AgentPhase.IDLE
        agent._runtime = SimpleNamespace()
        agent.hooks = Hooks()
        agent._session_manager = SimpleNamespace(append_compaction=lambda **kwargs: "entry")
        agent._run_compaction = AsyncMock(
            return_value=(
                CompactionResult(summary="summary", first_kept_entry_id="kept", tokens_before=100),
                False,
            )
        )
        asyncio.run(agent._apply_compaction(SimpleNamespace(), [], manual=manual))

    run(manual=False)
    assert notifications == []
    run(manual=True)
    assert notifications == ["Compaction completed."]


def test_context_usage_drops_immediately_after_compaction() -> None:
    """Right after compaction the kept messages still carry pre-compaction
    usage; the reported context size must come from the effective
    (summary + kept) list, not that stale anchor."""
    from tau.message.types import AssistantMessage, CompactionSummaryMessage, Usage
    from tau.session.types import CompactionEntry

    agent = _agent(_Settings(), context_window=200_000)
    kept = AssistantMessage.from_text("done")
    kept.usage = Usage(input_tokens=150_000, output_tokens=10)
    kept.timestamp = 100.0
    summary = CompactionSummaryMessage(summary="short summary of the history")
    entry = CompactionEntry(
        summary="short summary of the history",
        first_kept_entry_id="kept",
        tokens_before=150_000,
        timestamp=200.0,
    )
    agent._session_manager = SimpleNamespace(
        build_session_context=lambda: SimpleNamespace(messages=[summary, kept]),
        get_branch=lambda: [entry],
    )

    agent.update_context_tokens()

    assert agent._context_tokens < 10_000


def test_context_usage_keeps_fresh_anchor() -> None:
    """A usage anchor from a response after the last compaction stays authoritative."""
    from tau.message.types import AssistantMessage, Usage
    from tau.session.types import CompactionEntry

    agent = _agent(_Settings(), context_window=200_000)
    fresh = AssistantMessage.from_text("done")
    fresh.usage = Usage(input_tokens=42_000, output_tokens=10)
    fresh.timestamp = 300.0
    entry = CompactionEntry(
        summary="old summary",
        first_kept_entry_id="kept",
        tokens_before=150_000,
        timestamp=200.0,
    )
    agent._session_manager = SimpleNamespace(
        build_session_context=lambda: SimpleNamespace(messages=[fresh]),
        get_branch=lambda: [entry],
    )

    agent.update_context_tokens()

    assert agent._context_tokens >= 42_000


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


def test_threshold_compaction_resumes_with_rebuilt_context() -> None:
    async def scenario() -> None:
        agent: Any = Agent.__new__(Agent)
        compacted = UserMessage()
        agent._check_compaction = AsyncMock(return_value=True)
        agent._session_manager = SimpleNamespace(
            build_session_context=lambda: SimpleNamespace(messages=[compacted]),
        )

        result = await agent._transform_context([], None)

        assert result == [compacted]
        agent._check_compaction.assert_awaited_once()

    asyncio.run(scenario())


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


def test_messages_queued_by_save_point_handler_are_continued() -> None:
    async def scenario() -> None:
        pending = False
        continuation_count = 0
        agent: Any = Agent.__new__(Agent)
        agent._phase = AgentPhase.IDLE
        agent._idle_event = asyncio.Event()
        agent._idle_event.set()
        agent._signal = asyncio.Event()
        agent._overflow_recovery_attempted = False
        agent._session_manager = SimpleNamespace(append_message=lambda *args, **kwargs: "entry")
        agent._engine = SimpleNamespace(
            llm=SimpleNamespace(api=SimpleNamespace(options=SimpleNamespace(signal=None))),
            has_pending_messages=lambda: pending,
        )
        agent.hooks = Hooks()
        agent._build_turn_context = lambda: SimpleNamespace()
        agent._run = AsyncMock()
        agent._check_compaction = AsyncMock(return_value=False)

        async def continue_turn() -> None:
            nonlocal continuation_count, pending
            continuation_count += 1
            pending = False

        save_points = 0

        async def queue_once(event) -> None:
            nonlocal pending, save_points
            save_points += 1
            if save_points == 1:
                pending = True

        agent._run_continue = continue_turn
        agent.hooks.register("save_point", queue_once)

        await agent.invoke("hello")

        assert continuation_count == 1
        assert save_points == 2

    asyncio.run(scenario())


def test_ephemeral_injection_reuses_transform_context_session_ctx() -> None:
    """Engine._loop calls transform_context() then ephemeral_injection() back
    to back on every turn, with nothing in between that touches session
    state — so build_session_context() (walks the whole branch chain, no
    caching) must run once per turn, not twice.
    """

    async def scenario() -> None:
        build_calls = 0

        def build_session_context() -> Any:
            nonlocal build_calls
            build_calls += 1
            return SimpleNamespace(messages=[])

        agent: Any = Agent.__new__(Agent)
        agent._abort_requested = False
        agent._compaction_failures = 3  # short-circuits _check_compaction before it
        # would otherwise call build_session_context() too (see _check_compaction —
        # that call is *not* redundant with transform_context's, since compaction
        # can mutate the branch in between; this test isolates the one that is).
        agent._session_manager = SimpleNamespace(build_session_context=build_session_context)
        agent.hooks = Hooks()

        await agent._transform_context([], None)
        assert build_calls == 1

        await agent._ephemeral_injection()
        assert build_calls == 1, "ephemeral_injection() must reuse transform_context()'s context"

    asyncio.run(scenario())


def test_ephemeral_injection_falls_back_when_called_without_transform_context() -> None:
    """If ephemeral_injection() is ever invoked without a preceding
    transform_context() this turn (Engine's callback slots are independently
    configurable, even though Agent always wires both), it must build its own
    context rather than silently reusing a stale one from a previous turn.
    """

    async def scenario() -> None:
        build_calls = 0

        def build_session_context() -> Any:
            nonlocal build_calls
            build_calls += 1
            return SimpleNamespace(messages=[])

        agent: Any = Agent.__new__(Agent)
        agent._session_manager = SimpleNamespace(build_session_context=build_session_context)
        agent.hooks = Hooks()
        agent._pending_session_ctx = None

        await agent._ephemeral_injection()

        assert build_calls == 1

    asyncio.run(scenario())
