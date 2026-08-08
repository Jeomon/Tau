"""Regression test: RuntimeContext.create() must not block the event loop
while loading an existing session file.

SessionManager's constructor does synchronous file I/O + per-line JSON
parsing when given a session_file (resume, /session switch, tree navigation
across sessions) — for a large session (many turns, sizable tool-call
results) this is real, measured cost, not the sub-millisecond in-memory work
the rest of RuntimeContext.create() otherwise does. Confirmed via direct
profiling: ~150ms for a 5000-turn/~16MB session. Run synchronously on the
event loop, that freezes the whole TUI (no render, no input) for that span —
exactly the class of problem this codebase already solved for git-status
subprocess calls in the same function (see the `git_task` comment right
above the session-manager block in tau/runtime/types.py).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.hooks.service import Hooks
from tau.message.types import (
    AssistantMessage,
    TextContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)
from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.types import RuntimeConfig, RuntimeContext
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.tool.registry import ToolRegistry


def _build_large_session_file(cwd: Path, n_turns: int, tool_result_chars: int) -> Path:
    sm = SessionManager(cwd=cwd, persist=True)
    tool_text = "x" * tool_result_chars
    for i in range(n_turns):
        sm.append_message(UserMessage(contents=[TextContent(content=f"do thing {i}")]))
        sm.append_message(AssistantMessage(contents=[TextContent(content=f"working on {i} " * 20)]))
        sm.append_message(
            ToolMessage(
                contents=[
                    ToolResultContent(
                        id=f"call_{i}", content=tool_text, tool_name="read", is_error=False
                    )
                ]
            )
        )
    assert sm.session_file is not None
    return sm.session_file


class _Options:
    timeout = None
    max_retries = 0
    retry_base_delay_ms = 0


class _FakeLLM:
    def __init__(self) -> None:
        self.model = SimpleNamespace(thinking=False, input_limit=100_000)
        self.api = SimpleNamespace(options=_Options())
        self.provider_id = "fake"


@pytest.mark.asyncio
async def test_session_loading_does_not_block_the_event_loop(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    # Large enough that a synchronous load takes long enough to give a wide,
    # non-flaky margin over the handful of heartbeats that fire during
    # create()'s other async setup regardless of this fix (settings, LLM,
    # git-status task) — matches the profiled ~150ms scale for 5000 turns.
    session_file = _build_large_session_file(session_dir, n_turns=5000, tool_result_chars=2000)

    ticks: list[float] = []

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(0.005)
            ticks.append(time.perf_counter())

    config = RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        session_file=session_file,
        persist_session=True,
        project_trusted=True,
        dependencies=RuntimeDependencies(
            settings=lambda ctx: SettingsManager.create(
                ctx.cwd, config_dir=ctx.config_dir, project_trusted=ctx.project_trusted
            ),
            llm=lambda ctx: _FakeLLM(),  # type: ignore[arg-type]
            hooks=lambda: Hooks(),
            tool_registry=lambda: ToolRegistry(),
        ),
    )

    heartbeat_task = asyncio.ensure_future(_heartbeat())
    started = time.perf_counter()
    try:
        context = await RuntimeContext.create(config)
    finally:
        elapsed = time.perf_counter() - started
        heartbeat_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat_task

    assert context.session_manager is not None
    assert len(context.session_manager.entries) > 3000
    assert ticks, "the heartbeat never ran at all"

    # The real assertion: no single stall dominated create(). A synchronous
    # load would block the loop for the whole ~150ms read, leaving one gap
    # about as long as create() itself; loading in a thread leaves the loop
    # free, so the longest gap is just sleep granularity.
    #
    # Deliberately a *ratio* rather than a heartbeat count. A count is a
    # proxy for speed as much as for blocking: on a loaded runner
    # asyncio.sleep(0.005) can take several times as long, thinning the
    # ticks until a passing run looks like a blocked one. Both sides of a
    # ratio scale with the machine, so it measures what it claims to.
    gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:], strict=False)]
    longest_stall = max([*gaps, ticks[0] - started])

    assert longest_stall < elapsed * 0.5, (
        f"the event loop stalled for {longest_stall * 1000:.0f}ms of a "
        f"{elapsed * 1000:.0f}ms create() — the session load blocked it "
        "instead of running in a thread"
    )
