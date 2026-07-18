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
