from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from tau.hooks.runtime import RuntimeReadyEvent
from tau.hooks.service import Hooks
from tau.message.types import UserMessage
from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig


class _Engine:
    def __init__(self) -> None:
        self.steering: list[UserMessage] = []
        self.followups: list[UserMessage] = []

    async def steer(self, message: UserMessage) -> None:
        self.steering.append(message)

    async def follow_up(self, message: UserMessage) -> None:
        self.followups.append(message)


def _runtime(config: RuntimeConfig | None = None) -> tuple[Runtime, _Engine, Hooks]:
    runtime = object.__new__(Runtime)
    engine = _Engine()
    hooks = Hooks()
    runtime._config = config or RuntimeConfig(cwd=Path.cwd())
    runtime._context = SimpleNamespace(
        engine=engine,
        hooks=hooks,
        resource_snapshot=None,
    )
    return runtime, engine, hooks


def test_runtime_exposes_event_subscription() -> None:
    runtime, _engine, hooks = _runtime()
    events: list[str] = []

    unsubscribe = runtime.subscribe(lambda event: events.append(event.type))
    asyncio.run(hooks.emit(RuntimeReadyEvent()))
    unsubscribe()
    asyncio.run(hooks.emit(RuntimeReadyEvent()))

    assert events == ["runtime_ready"]


def test_runtime_exposes_steering_and_follow_up() -> None:
    runtime, engine, _hooks = _runtime()

    asyncio.run(runtime.steer("redirect"))
    asyncio.run(runtime.follow_up("then continue"))

    assert engine.steering[0].contents[0].content == "redirect"  # type: ignore[union-attr]
    assert engine.followups[0].contents[0].content == "then continue"  # type: ignore[union-attr]


def test_runtime_tool_filters() -> None:
    config = RuntimeConfig(
        cwd=Path.cwd(),
        tool_allowlist={"read", "write"},
        exclude_tools={"write"},
    )
    runtime, _engine, _hooks = _runtime(config)

    assert runtime._tool_enabled("read")
    assert not runtime._tool_enabled("write")
    assert not runtime._tool_enabled("terminal")
