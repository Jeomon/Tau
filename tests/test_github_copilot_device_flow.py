"""Tests for the GitHub Copilot device-code poll loop.

`_poll_for_github_token` had no direct coverage, and it is the only one of the
three device flows with poll-interval multipliers and a timeout message that
depends on what happened during the loop — the two things most easily lost when
its timing moved into the shared DeviceCodePoller.
"""

from __future__ import annotations

import pytest

import tau.inference.provider.oauth.device_code as device_code
import tau.inference.provider.oauth.github_copilot as copilot


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(device_code.time, "time", c.time)
    monkeypatch.setattr(device_code.asyncio, "sleep", c.sleep)
    return c


def _responses(monkeypatch, *payloads: dict) -> dict[str, int]:
    """Serve `payloads` in order, repeating the last one forever."""
    calls = {"n": 0}

    def fake_poll(domain: str, device_code_value: str) -> dict:
        assert domain == "github.com"
        assert device_code_value == "dev-code"
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return payload

    monkeypatch.setattr(copilot, "_poll_access_token_once", fake_poll)
    return calls


@pytest.mark.asyncio
async def test_pending_then_slow_down_then_token(clock: _Clock, monkeypatch) -> None:
    calls = _responses(
        monkeypatch,
        {"error": "authorization_pending"},
        {"error": "slow_down"},
        {"access_token": "gho_token"},
    )

    token = await copilot._poll_for_github_token("github.com", "dev-code", 5, 900)

    assert token == "gho_token"
    assert calls["n"] == 3
    # 1.2x the stated interval while waiting, then 1.4x of a 5s-widened
    # interval once GitHub asks us to back off.
    assert clock.sleeps == [6.0, 6.0, 14.0]


@pytest.mark.asyncio
async def test_server_supplied_interval_is_honoured_on_slow_down(
    clock: _Clock, monkeypatch
) -> None:
    _responses(
        monkeypatch,
        {"error": "slow_down", "interval": 30},
        {"access_token": "gho_token"},
    )

    await copilot._poll_for_github_token("github.com", "dev-code", 5, 900)

    assert clock.sleeps == [6.0, 42.0]  # 30s * the 1.4x slow-down multiplier


@pytest.mark.asyncio
async def test_error_response_raises_with_the_description(clock: _Clock, monkeypatch) -> None:
    _responses(monkeypatch, {"error": "access_denied", "error_description": "user said no"})

    with pytest.raises(RuntimeError, match="Device flow failed: access_denied: user said no"):
        await copilot._poll_for_github_token("github.com", "dev-code", 5, 900)


@pytest.mark.asyncio
async def test_timeout_without_slow_down_is_reported_plainly(clock: _Clock, monkeypatch) -> None:
    _responses(monkeypatch, {"error": "authorization_pending"})

    with pytest.raises(RuntimeError, match="^Device flow timed out$"):
        await copilot._poll_for_github_token("github.com", "dev-code", 5, 20)


@pytest.mark.asyncio
async def test_timeout_after_a_slow_down_blames_clock_drift(clock: _Clock, monkeypatch) -> None:
    """The WSL/VM hint is only useful when a slow_down actually arrived."""
    _responses(monkeypatch, {"error": "slow_down"}, {"error": "authorization_pending"})

    with pytest.raises(RuntimeError, match="clock drift"):
        await copilot._poll_for_github_token("github.com", "dev-code", 5, 60)
