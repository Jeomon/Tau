"""Regression tests for agent compaction settings and circuit breaking."""

from __future__ import annotations

from types import SimpleNamespace

from tau.agent.service import Agent
from tau.session.compaction import CompactionSettings


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


def _agent(settings: _Settings | None, context_window: int = 10_000) -> Agent:
    agent = Agent.__new__(Agent)
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
