"""Tests for the shared RFC 8628 poll timing in tau/inference/provider/oauth/device_code.py.

This arithmetic used to be copied into the GitHub Copilot, OpenAI Codex and
xAI Grok logins, where it was effectively untested: exercising it meant driving
a whole login flow. Now that all three share it, the timing itself is worth
pinning down — a regression here silently hammers three providers' token
endpoints or stalls three logins.

The clock is faked rather than slept through: sleeping advances a counter, so
the deadline arithmetic is exercised exactly, instantly, and deterministically.
"""

from __future__ import annotations

import asyncio

import pytest

import tau.inference.provider.oauth.device_code as device_code
from tau.inference.provider.oauth.device_code import DeviceCodePoller


class _Clock:
    """Fake monotonic-ish clock whose only way to advance is sleeping."""

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


async def _drain(poller: DeviceCodePoller, *, stop_after: int | None = None) -> int:
    ticks = 0
    async for _ in poller:
        ticks += 1
        if stop_after is not None and ticks >= stop_after:
            break
    return ticks


@pytest.mark.asyncio
async def test_waits_before_the_first_attempt(clock: _Clock) -> None:
    """RFC 8628: the user needs time to enter the code somewhere else, so the
    first poll must not fire the instant the device code is issued."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=100)
    await _drain(poller, stop_after=1)
    assert clock.sleeps == [5.0]


@pytest.mark.asyncio
async def test_polls_at_the_requested_interval(clock: _Clock) -> None:
    poller = DeviceCodePoller(interval_seconds=5, expires_in=100)
    await _drain(poller, stop_after=3)
    assert clock.sleeps == [5.0, 5.0, 5.0]


@pytest.mark.asyncio
async def test_interval_has_a_one_second_floor(clock: _Clock) -> None:
    """A server that sends interval 0 (xAI does, in tests) must not turn the
    loop into a busy-wait."""
    poller = DeviceCodePoller(interval_seconds=0, expires_in=100)
    await _drain(poller, stop_after=2)
    assert clock.sleeps == [1.0, 1.0]


@pytest.mark.asyncio
async def test_multiplier_pads_the_interval(clock: _Clock) -> None:
    """GitHub polls at 1.2x the stated interval."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=100, interval_multiplier=1.2)
    await _drain(poller, stop_after=2)
    assert clock.sleeps == [6.0, 6.0]


@pytest.mark.asyncio
async def test_iteration_ends_at_the_deadline(clock: _Clock) -> None:
    """The caller raises its own timeout error, so the loop just has to stop."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=12)
    assert await _drain(poller) == 3  # 5s, 5s, then 2s of remaining time
    assert clock.sleeps == [5.0, 5.0, 2.0]


@pytest.mark.asyncio
async def test_never_sleeps_past_the_deadline(clock: _Clock) -> None:
    """Otherwise the final wait overshoots by most of an interval before the
    caller can report the timeout."""
    poller = DeviceCodePoller(interval_seconds=30, expires_in=10)
    await _drain(poller)
    assert clock.sleeps == [10.0]
    assert clock.now == 1010.0


@pytest.mark.asyncio
async def test_slow_down_adopts_a_server_supplied_interval(clock: _Clock) -> None:
    poller = DeviceCodePoller(interval_seconds=5, expires_in=1000)
    ticks = 0
    async for tick in poller:
        ticks += 1
        if ticks == 1:
            tick.slow_down(20)
        if ticks == 3:
            break
    assert clock.sleeps == [5.0, 20.0, 20.0]


@pytest.mark.asyncio
async def test_slow_down_without_an_interval_adds_five_seconds(clock: _Clock) -> None:
    """The Codex endpoint never restates an interval."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=1000)
    ticks = 0
    async for tick in poller:
        ticks += 1
        tick.slow_down()
        if ticks == 3:
            break
    assert clock.sleeps == [5.0, 10.0, 15.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("useless", [0, -1, None, "5", True])
async def test_slow_down_ignores_a_useless_server_interval(clock: _Clock, useless) -> None:
    """Zero, negative, missing and non-numeric all fall back to the fixed
    increment. `True` is an int in Python and must not become a 1s interval."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=1000)
    ticks = 0
    async for tick in poller:
        ticks += 1
        if ticks == 1:
            tick.slow_down(useless)
        if ticks == 2:
            break
    assert clock.sleeps == [5.0, 10.0]


@pytest.mark.asyncio
async def test_slow_down_switches_to_the_slow_down_multiplier(clock: _Clock) -> None:
    """GitHub widens its padding from 1.2x to 1.4x once told to back off."""
    poller = DeviceCodePoller(
        interval_seconds=5,
        expires_in=1000,
        interval_multiplier=1.2,
        slow_down_multiplier=1.4,
    )
    ticks = 0
    async for tick in poller:
        ticks += 1
        if ticks == 1:
            tick.slow_down(10)
        if ticks == 2:
            break
    assert clock.sleeps == [6.0, 14.0]


@pytest.mark.asyncio
async def test_slow_downs_are_counted(clock: _Clock) -> None:
    """Copilot reports a timeout that followed a slow_down as likely clock drift."""
    poller = DeviceCodePoller(interval_seconds=5, expires_in=1000)
    ticks = 0
    async for tick in poller:
        ticks += 1
        tick.slow_down()
        if ticks == 2:
            break
    assert poller.slow_downs == 2


@pytest.mark.asyncio
async def test_abort_signal_stops_the_loop(clock: _Clock) -> None:
    signal = asyncio.Event()
    signal.set()
    poller = DeviceCodePoller(
        interval_seconds=5, expires_in=100, signal=signal, abort_message="nope, aborted"
    )
    with pytest.raises(RuntimeError, match="nope, aborted"):
        await _drain(poller)
    assert clock.sleeps == [], "abort must be checked before waiting out an interval"


@pytest.mark.asyncio
async def test_abort_mid_flight_stops_the_loop(clock: _Clock) -> None:
    signal = asyncio.Event()
    poller = DeviceCodePoller(interval_seconds=5, expires_in=1000, signal=signal)
    ticks = 0
    with pytest.raises(RuntimeError, match="aborted"):
        async for _ in poller:
            ticks += 1
            signal.set()
    assert ticks == 1


@pytest.mark.asyncio
async def test_expired_poller_yields_nothing(clock: _Clock) -> None:
    poller = DeviceCodePoller(interval_seconds=5, expires_in=0)
    assert await _drain(poller) == 0
    assert clock.sleeps == []
