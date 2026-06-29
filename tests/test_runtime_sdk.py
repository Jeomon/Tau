from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tau.hooks.runtime import RuntimeReadyEvent
from tau.hooks.service import Hooks
from tau.message.types import (
    AssistantMessage,
    AudioContent,
    ImageContent,
    SystemMessage,
    TextContent,
    UserMessage,
    VideoContent,
)
from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.service import Runtime
from tau.runtime.types import RuntimeConfig, RuntimeContext, _seed_initial_messages
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.tool.registry import ToolRegistry


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


def test_runtime_dependency_factories_are_used(tmp_path: Path) -> None:
    hooks = Hooks()
    registry = ToolRegistry()
    calls: dict[str, Any] = {}
    counts = {"settings": 0, "llm": 0, "session": 0, "hooks": 0, "registry": 0}

    class _Options:
        timeout = None
        max_retries = 0
        retry_base_delay_ms = 0

    class _LLM:
        def __init__(self) -> None:
            self.model = SimpleNamespace(thinking=False, input_limit=100_000)
            self.api = SimpleNamespace(options=_Options())

    def settings_factory(context):
        counts["settings"] += 1
        calls["settings"] = context
        return SettingsManager.create(
            context.cwd,
            config_dir=context.config_dir,
            project_trusted=context.project_trusted,
        )

    def llm_factory(context):
        counts["llm"] += 1
        calls["llm"] = context
        return _LLM()

    def session_factory(context):
        counts["session"] += 1
        calls["session"] = context
        return SessionManager(
            cwd=context.cwd,
            session_dir=context.session_dir,
            session_file=context.session_file,
            persist=context.persist,
        )

    def hooks_factory():
        counts["hooks"] += 1
        return hooks

    def registry_factory():
        counts["registry"] += 1
        return registry

    config = RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        persist_session=False,
        project_trusted=True,
        dependencies=RuntimeDependencies(
            settings=settings_factory,
            llm=llm_factory,  # type: ignore[arg-type]
            session_manager=session_factory,
            hooks=hooks_factory,
            tool_registry=registry_factory,
        ),
    )

    context = asyncio.run(RuntimeContext.create(config))

    assert context.hooks is hooks
    assert context.tool_registry is registry
    assert context.llm is not None
    assert calls["settings"].cwd == tmp_path
    assert calls["llm"].model_id
    assert calls["session"].persist is False

    replacement = asyncio.run(
        RuntimeContext.create(
            config,
            settings_manager=context.settings_manager,
            hooks=context.hooks,
            ext_runtime=context.ext_runtime,
        )
    )

    assert replacement.session_manager is not context.session_manager
    assert replacement.hooks is hooks
    assert counts == {
        "settings": 1,
        "llm": 2,
        "session": 2,
        "hooks": 1,
        "registry": 2,
    }


def test_runtime_config_seeds_initial_messages_and_media(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path, persist=False)
    config = RuntimeConfig(
        cwd=tmp_path,
        initial_messages=[
            SystemMessage.text("Existing instructions"),
            AssistantMessage.from_text("Existing response"),
        ],
        initial_prompt="Inspect these inputs",
        initial_images=[b"\x89PNG\r\n\x1a\n"],
        initial_audio=[b"audio"],
        initial_video=[b"video"],
    )

    _seed_initial_messages(manager, config)

    messages = manager.build_session_context().messages
    assert len(messages) == 3
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert isinstance(messages[2], UserMessage)
    assert [type(content) for content in messages[2].contents] == [
        TextContent,
        ImageContent,
        AudioContent,
        VideoContent,
    ]


def test_runtime_config_allows_media_without_text(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path, persist=False)
    config = RuntimeConfig(cwd=tmp_path, initial_images=[b"\x89PNG\r\n\x1a\n"])

    _seed_initial_messages(manager, config)

    message = manager.build_session_context().messages[0]
    assert isinstance(message, UserMessage)
    assert isinstance(message.contents[0], TextContent)
    assert message.contents[0].content == ""
    assert isinstance(message.contents[1], ImageContent)
