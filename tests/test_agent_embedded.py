"""Regression tests for run_embedded_agent lifecycle handling.

An external CancelledError (e.g. the parent engine's tool timeout firing
before TASK_TIMEOUT_S) used to propagate straight through the shielded
wait_for, leaving the embedded engine's run task alive and detached — still
streaming the LLM and executing tools with its results discarded. Cancelling
the caller must tear the embedded engine down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tau.agent.embedded as embedded


class _HangingEngine:
    """Engine double whose run() blocks forever until cancelled."""

    last: _HangingEngine | None = None

    def __init__(self, **kwargs) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        _HangingEngine.last = self

    async def run(self, ctx, signal=None) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _SignalAwareEngine:
    """Engine double whose run() records its signal and blocks until it is set."""

    last: _SignalAwareEngine | None = None

    def __init__(self, **kwargs) -> None:
        self.started = asyncio.Event()
        self.signal: asyncio.Event | None = None
        _SignalAwareEngine.last = self

    async def run(self, ctx, signal=None) -> None:
        assert signal is not None
        self.signal = signal
        self.started.set()
        # Unwind cooperatively when aborted, like the real engine does.
        await signal.wait()


def test_timeout_does_not_set_the_callers_abort_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A subagent timeout must abort only the embedded engine.

    The caller's abort_signal is typically the parent engine's own abort
    signal (Tool.execute passes it straight through), so setting it on a
    timeout used to abort the whole parent session — surfacing in the TUI as
    a spurious "User Interrupted" with no user action.
    """
    _SignalAwareEngine.last = None
    monkeypatch.setattr(embedded, "TextLLM", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(embedded, "Engine", _SignalAwareEngine)

    async def scenario() -> None:
        parent_signal = asyncio.Event()
        ok, output, _usage = await embedded.run_embedded_agent(
            cwd=tmp_path,
            model_id="m",
            provider="p",
            system_prompt="s",
            tool_names=["read"],
            task_text="do the thing",
            abort_signal=parent_signal,
            timeout_s=0.05,
        )
        assert not ok and "timed out" in output
        engine = _SignalAwareEngine.last
        assert engine is not None and engine.signal is not None
        # The embedded engine was aborted via its own local signal...
        assert engine.signal.is_set()
        assert engine.signal is not parent_signal
        # ...and the parent's signal was left untouched.
        assert not parent_signal.is_set()

    asyncio.run(scenario())


def test_caller_abort_signal_propagates_into_the_embedded_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _SignalAwareEngine.last = None
    monkeypatch.setattr(embedded, "TextLLM", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(embedded, "Engine", _SignalAwareEngine)

    async def scenario() -> None:
        parent_signal = asyncio.Event()
        task = asyncio.create_task(
            embedded.run_embedded_agent(
                cwd=tmp_path,
                model_id="m",
                provider="p",
                system_prompt="s",
                tool_names=["read"],
                task_text="do the thing",
                abort_signal=parent_signal,
                timeout_s=30,
            )
        )
        engine = None
        for _ in range(200):
            engine = _SignalAwareEngine.last
            if engine is not None and engine.started.is_set():
                break
            await asyncio.sleep(0.005)
        assert engine is not None and engine.started.is_set()

        # Parent-side abort (e.g. Esc in the TUI) must still stop the child.
        parent_signal.set()
        await asyncio.wait_for(task, timeout=1)
        assert engine.signal is not None and engine.signal.is_set()

    asyncio.run(scenario())


def test_preset_caller_abort_signal_aborts_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _SignalAwareEngine.last = None
    monkeypatch.setattr(embedded, "TextLLM", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(embedded, "Engine", _SignalAwareEngine)

    async def scenario() -> None:
        parent_signal = asyncio.Event()
        parent_signal.set()
        await asyncio.wait_for(
            embedded.run_embedded_agent(
                cwd=tmp_path,
                model_id="m",
                provider="p",
                system_prompt="s",
                tool_names=["read"],
                task_text="do the thing",
                abort_signal=parent_signal,
                timeout_s=30,
            ),
            timeout=1,
        )
        engine = _SignalAwareEngine.last
        assert engine is not None and engine.signal is not None and engine.signal.is_set()

    asyncio.run(scenario())


def test_external_cancellation_tears_down_the_embedded_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _HangingEngine.last = None
    monkeypatch.setattr(embedded, "TextLLM", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(embedded, "Engine", _HangingEngine)

    async def scenario() -> None:
        task = asyncio.create_task(
            embedded.run_embedded_agent(
                cwd=tmp_path,
                model_id="m",
                provider="p",
                system_prompt="s",
                tool_names=["read"],
                task_text="do the thing",
            )
        )
        engine = None
        for _ in range(200):
            engine = _HangingEngine.last
            if engine is not None and engine.started.is_set():
                break
            await asyncio.sleep(0.005)
        assert engine is not None and engine.started.is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The orphaned engine run must have been cancelled too, not left detached.
        await asyncio.wait_for(engine.cancelled.wait(), timeout=1)

    asyncio.run(scenario())
