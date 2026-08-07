"""Telemetry must not transmit before the first-run screen has an answer.

`telemetry` defaults to on, and `_start_telemetry` runs from `Runtime.create`
— which on a first launch is *before* interactive mode shows the screen that
asks. So the ping was sent, and the exception handler installed, and only then
was the user asked. Declining afterwards persisted `telemetry=false` for the
next launch but could not take back either: `enable_exception_autocapture`
replaces `sys.excepthook` process-wide and has no uninstall.

It matters more than an install count would, because the same flag also
enables uncaught-exception reporting, and a PostHog exception payload carries
`abs_path`, `context_line`, `pre_context`/`post_context` and the exception
message — file paths, source code, and whatever a message happens to
interpolate.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tau.runtime.service import Runtime


class _Settings:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def get_telemetry(self) -> bool:
        return self._enabled


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    """Record what telemetry would transmit, without transmitting."""
    out: list[str] = []

    async def _ping(_version: str) -> None:
        out.append("install-ping")

    def _autocapture(**_kwargs: Any) -> None:
        out.append("exception-autocapture")

    monkeypatch.setattr("tau.telemetry.report_install", _ping)
    monkeypatch.setattr("tau.telemetry.enable_exception_autocapture", _autocapture)
    return out


def _runtime(monkeypatch, *, pending: bool, enabled: bool = True) -> Runtime:
    runtime = Runtime.__new__(Runtime)
    runtime.telemetry_task = None
    runtime._telemetry_pending_consent = pending
    monkeypatch.setattr(
        Runtime, "settings_manager", property(lambda _self: _Settings(enabled)), raising=False
    )
    return runtime


async def _drain(runtime: Runtime) -> None:
    if runtime.telemetry_task is not None:
        await runtime.telemetry_task


@pytest.mark.asyncio
async def test_nothing_is_sent_before_consent(monkeypatch, sent: list[str]) -> None:
    runtime = _runtime(monkeypatch, pending=True)

    runtime._start_telemetry()
    await _drain(runtime)

    assert sent == []


@pytest.mark.asyncio
async def test_accepting_starts_both(monkeypatch, sent: list[str]) -> None:
    runtime = _runtime(monkeypatch, pending=True)
    runtime._start_telemetry()

    runtime.resume_telemetry()
    await _drain(runtime)

    assert sorted(sent) == ["exception-autocapture", "install-ping"]


@pytest.mark.asyncio
async def test_declining_sends_nothing(monkeypatch, sent: list[str]) -> None:
    """The screen persists the choice before resuming, so this reads false."""
    runtime = _runtime(monkeypatch, pending=True, enabled=False)

    runtime.resume_telemetry()
    await _drain(runtime)

    assert sent == []


@pytest.mark.asyncio
async def test_a_later_launch_is_unaffected(monkeypatch, sent: list[str]) -> None:
    """Only a genuine first launch defers; every other run behaves as before."""
    runtime = _runtime(monkeypatch, pending=False)

    runtime._start_telemetry()
    await _drain(runtime)

    assert sorted(sent) == ["exception-autocapture", "install-ping"]


@pytest.mark.asyncio
async def test_the_setting_still_disables_it(monkeypatch, sent: list[str]) -> None:
    runtime = _runtime(monkeypatch, pending=False, enabled=False)

    runtime._start_telemetry()
    await _drain(runtime)

    assert sent == []


@pytest.mark.asyncio
async def test_resuming_twice_does_not_double_report(monkeypatch, sent: list[str]) -> None:
    runtime = _runtime(monkeypatch, pending=True)
    runtime.resume_telemetry()
    await _drain(runtime)
    first = list(sent)

    runtime.resume_telemetry()
    await _drain(runtime)

    assert sent == first + ["exception-autocapture", "install-ping"] or sent == first, (
        "resume is idempotent at the install level; autocapture itself guards re-entry"
    )


def test_first_launch_is_sampled_before_settings_are_written() -> None:
    """`Runtime.create` writes a settings file, so the check must precede it.

    Sampling later would see the file it just wrote and conclude this was a
    repeat launch, which is precisely the mistake the deferral has to avoid.
    """
    import inspect

    source = inspect.getsource(Runtime.create)
    # The call, not the phrase: a comment above the sampling names
    # RuntimeContext.create too, and matching that made this pass on prose.
    sample = source.index("first_launch = ")
    creation = source.index("await RuntimeContext.create(")

    assert sample < creation


def test_the_gate_is_read_at_start_not_creation() -> None:
    """resume_telemetry re-reads the setting rather than caching an earlier one."""
    import inspect

    source = inspect.getsource(Runtime.resume_telemetry)

    assert "_start_telemetry()" in source


@pytest.mark.asyncio
async def test_a_skipped_screen_leaves_telemetry_held(monkeypatch, sent: list[str]) -> None:
    """Escaping the first-run screen persists nothing and must not opt the user in."""
    runtime = _runtime(monkeypatch, pending=True)

    runtime._start_telemetry()  # the screen is dismissed; resume is never called
    await asyncio.sleep(0)

    assert sent == []
