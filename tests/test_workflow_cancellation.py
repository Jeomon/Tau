"""Cancellation wiring for the workflow extension.

A /workflow run executes while the agent is idle, so the normal Esc-abort
path (agent busy → agent.abort()) can never reach it — historically a
running workflow was uninterruptible short of quitting tau. Cancellation is
now cooperative: the command registers an abort signal with the runtime's
cancellable-operation registry (idle-agent Esc/Ctrl+C sets it), the runner
stops starting new tasks once it is set, and the signal is forwarded into
every embedded agent run so in-flight tasks abort too.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

from tau.runtime.service import Runtime
from tests.ext_loader import load_extension

_PKG = load_extension("workflow").__name__
runner = importlib.import_module(f"{_PKG}.runner")
model = importlib.import_module(f"{_PKG}.model")


def _wf(n_phases: int = 2) -> object:
    return model.WorkflowDef(
        meta=model.WorkflowMeta(name="wf"),
        phases=[
            model.WorkflowPhase(
                title=f"Phase {i + 1}",
                tasks=[model.WorkflowTask(agent="worker", task=f"task {i + 1}")],
            )
            for i in range(n_phases)
        ],
    )


class _Agent:
    name = "worker"
    system_prompt = "s"
    tools = None


def _usage() -> dict:
    return {"turns": 1, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}


def test_abort_between_phases_stops_the_workflow(monkeypatch, tmp_path: Path) -> None:
    """A signal set during phase 1 must prevent phase 2 from starting."""
    abort = asyncio.Event()
    calls: list[str] = []

    async def fake_run(**kwargs):
        calls.append(kwargs["task_text"])
        abort.set()  # user presses Esc while task 1 runs; task 1 still finishes
        return True, "done", _usage()

    monkeypatch.setattr(runner, "run_embedded_agent", fake_run)

    result = asyncio.run(
        runner.run_workflow(
            _wf(2),
            cwd=tmp_path,
            model_id="m",
            provider="p",
            agents=[_Agent()],
            abort_signal=abort,
        )
    )
    assert not result.ok
    assert "cancelled" in result.error
    assert calls == ["task 1"]  # phase 2's task never started


def test_abort_signal_is_forwarded_to_embedded_agents(monkeypatch, tmp_path: Path) -> None:
    abort = asyncio.Event()
    seen: list[object] = []

    async def fake_run(**kwargs):
        seen.append(kwargs.get("abort_signal"))
        return True, "done", _usage()

    monkeypatch.setattr(runner, "run_embedded_agent", fake_run)

    result = asyncio.run(
        runner.run_workflow(
            _wf(1),
            cwd=tmp_path,
            model_id="m",
            provider="p",
            agents=[_Agent()],
            abort_signal=abort,
        )
    )
    assert result.ok
    assert seen == [abort]


def test_abort_mid_task_is_reported_as_cancelled(monkeypatch, tmp_path: Path) -> None:
    """An in-flight task aborted by the signal fails with 'cancelled', not a
    generic engine error like '(no output)'."""
    abort = asyncio.Event()

    async def fake_run(**kwargs):
        abort.set()
        return False, "(no output)", _usage()  # what an aborted engine run returns

    monkeypatch.setattr(runner, "run_embedded_agent", fake_run)

    result = asyncio.run(
        runner.run_workflow(
            _wf(1),
            cwd=tmp_path,
            model_id="m",
            provider="p",
            agents=[_Agent()],
            abort_signal=abort,
        )
    )
    assert not result.ok
    assert "cancelled" in result.error
    assert result.results[0].error == "cancelled"


def test_no_signal_keeps_legacy_behavior(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(**kwargs):
        return True, "done", _usage()

    monkeypatch.setattr(runner, "run_embedded_agent", fake_run)

    result = asyncio.run(
        runner.run_workflow(
            _wf(2), cwd=tmp_path, model_id="m", provider="p", agents=[_Agent()]
        )
    )
    assert result.ok
    assert len(result.results) == 2


# ── Runtime cancellable-operation registry ────────────────────────────────────


def _bare_runtime() -> Runtime:
    rt = object.__new__(Runtime)
    rt._cancellable_ops = []
    return rt


def test_cancel_active_operation_sets_most_recent_first() -> None:
    rt = _bare_runtime()
    first, second = asyncio.Event(), asyncio.Event()
    rt.register_cancellable("first op", first)
    rt.register_cancellable("second op", second)

    assert rt.cancel_active_operation() == "second op"
    assert second.is_set() and not first.is_set()
    # The entry stays registered (owner unregisters); the next cancel moves on
    # to the older, still-uncancelled operation.
    assert rt.cancel_active_operation() == "first op"
    assert first.is_set()
    assert rt.cancel_active_operation() is None


def test_unregister_removes_the_operation() -> None:
    rt = _bare_runtime()
    sig = asyncio.Event()
    unregister = rt.register_cancellable("op", sig)
    unregister()
    assert rt.cancel_active_operation() is None
    assert not sig.is_set()
    unregister()  # double-unregister is a no-op


def test_active_cancellable_signal_exposes_innermost_operation() -> None:
    rt = _bare_runtime()
    assert rt.active_cancellable_signal is None
    outer, inner = asyncio.Event(), asyncio.Event()
    rt.register_cancellable("outer", outer)
    assert rt.active_cancellable_signal is outer
    un_inner = rt.register_cancellable("inner", inner)
    assert rt.active_cancellable_signal is inner
    un_inner()
    assert rt.active_cancellable_signal is outer


def test_command_dispatch_auto_registers_an_ambient_signal() -> None:
    """user_input wraps every slash-command dispatch in a fresh cancellable
    signal — the ambient ctx.command_signal — and unregisters it afterwards,
    so extensions get Esc-cancellability with no registration ceremony."""
    from types import SimpleNamespace

    rt = _bare_runtime()
    seen: list[tuple[str, object]] = []

    async def dispatch(cmd):
        seen.append((cmd.name, rt.active_cancellable_signal))
        return True

    rt.commands = SimpleNamespace(dispatch=dispatch)
    asyncio.run(rt.user_input("/workflow demo"))

    assert len(seen) == 1
    name, signal = seen[0]
    assert name == "workflow"
    assert isinstance(signal, asyncio.Event)  # live during dispatch...
    assert rt.active_cancellable_signal is None  # ...gone after
