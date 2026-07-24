"""Abort-signal handling in the autoresearch benchmark tools.

run_experiment executes an arbitrary benchmark command with a default timeout
of 600s (plus 300s for checks.sh). The tool receives the engine's abort
signal but used to ignore it — so Esc mid-benchmark marked the turn aborted
while tau silently blocked on the subprocess until it finished or timed out.
The runner now races the subprocess against the signal and kills it when the
signal fires; an aborted run reports "no measurement taken" instead of a
timeout-crash, so the agent doesn't log a bogus experiment.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path
from types import SimpleNamespace

from tau.tool.types import ToolInvocation
from tests.ext_loader import load_extension

_PKG = load_extension("autoresearch").__name__
tools = importlib.import_module(f"{_PKG}.tools")
state_mod = importlib.import_module(f"{_PKG}.state")


def test_run_kills_the_subprocess_when_the_signal_fires(tmp_path: Path) -> None:
    async def scenario() -> None:
        signal = asyncio.Event()

        async def fire_soon() -> None:
            await asyncio.sleep(0.1)
            signal.set()

        started = time.monotonic()
        _, (code, _output, _seconds) = await asyncio.gather(
            fire_soon(), tools._run("sleep 30", tmp_path, timeout=60, signal=signal)
        )
        elapsed = time.monotonic() - started
        assert code is None  # killed, no valid measurement
        assert elapsed < 5  # did not wait out the sleep or the timeout

    asyncio.run(scenario())


def test_run_without_signal_completes_normally(tmp_path: Path) -> None:
    async def scenario() -> None:
        code, output, _seconds = await tools._run("echo hello", tmp_path, timeout=30)
        assert code == 0
        assert "hello" in output

    asyncio.run(scenario())


def test_run_with_unfired_signal_completes_normally(tmp_path: Path) -> None:
    async def scenario() -> None:
        signal = asyncio.Event()
        code, output, _seconds = await tools._run(
            "echo hello", tmp_path, timeout=30, signal=signal
        )
        assert code == 0
        assert "hello" in output

    asyncio.run(scenario())


def test_run_experiment_reports_abort_not_crash(tmp_path: Path) -> None:
    """An aborted benchmark must tell the agent not to log it — a timeout says
    'log this as a crash', which would record a bogus experiment."""
    session = SimpleNamespace(cwd=tmp_path, state=state_mod.State(), refresh=lambda: None)
    tool = tools.RunExperimentTool(session)
    signal = asyncio.Event()

    async def scenario():
        async def fire_soon() -> None:
            await asyncio.sleep(0.1)
            signal.set()

        invocation = ToolInvocation(
            id="t1", name="run_experiment", cwd=tmp_path, params={"command": "sleep 30"}
        )
        _, result = await asyncio.gather(fire_soon(), tool.execute(invocation, signal=signal))
        return result

    result = asyncio.run(scenario())
    assert not result.is_error
    assert "Aborted by user interrupt" in result.content
    assert "Do not log this" in result.content
    assert result.metadata == {"aborted": True, "seconds": result.metadata["seconds"]}
    assert "crash" not in result.content  # must not steer the agent into logging one
