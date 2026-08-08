from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tau.extensions.api import Extension, ExtensionAPI, ExtensionError
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
from tau.resources.types import ResourceDiagnostic, ResourceSnapshot
from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.service import Runtime
from tau.runtime.types import (
    RuntimeConfig,
    RuntimeContext,
    RuntimeStartupResult,
    _seed_initial_messages,
)
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


def test_new_session_clears_startup_resume_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = object.__new__(Runtime)
    runtime._config = RuntimeConfig(cwd=tmp_path, resume=True)
    runtime._context = SimpleNamespace(  # type: ignore[assignment]
        settings_manager=None,
        hooks=None,
        ext_runtime=None,
        agent=None,  # read by _settle_active_turn before the session is replaced
    )
    runtime._extension_generation = 0
    captured: list[RuntimeConfig] = []

    async def create_context(
        cls: type[RuntimeContext],
        config: RuntimeConfig,
        settings_manager: Any = None,
        hooks: Any = None,
        ext_runtime: Any = None,
    ) -> Any:
        captured.append(config)
        return SimpleNamespace()

    async def no_op(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(RuntimeContext, "create", classmethod(create_context))
    monkeypatch.setattr(runtime, "_emit_session_shutdown", no_op)
    monkeypatch.setattr(runtime, "_emit_session_start", no_op)
    monkeypatch.setattr(runtime, "_run_with_session", no_op)
    monkeypatch.setattr(runtime, "_reinit_after_context_create", lambda: None)

    asyncio.run(runtime.new_session())

    assert len(captured) == 1
    assert captured[0].session_file is None
    assert captured[0].resume is False


def test_new_session_settles_the_running_turn_before_rebuilding_the_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The old turn must be stopped *before* the context is replaced, or it
    keeps streaming into the session manager it was detached from."""
    agent = _TurnAgent()
    runtime = object.__new__(Runtime)
    runtime._config = RuntimeConfig(cwd=tmp_path)
    runtime._context = SimpleNamespace(  # type: ignore[assignment]
        settings_manager=None,
        hooks=None,
        ext_runtime=None,
        agent=agent,
    )
    runtime._extension_generation = 0
    runtime._extension_callback_depth = 0
    idle_at_rebuild: list[bool] = []

    async def create_context(
        cls: type[RuntimeContext], config: RuntimeConfig, **kwargs: Any
    ) -> Any:
        idle_at_rebuild.append(agent.is_idle())
        return SimpleNamespace()

    async def no_op(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(RuntimeContext, "create", classmethod(create_context))
    monkeypatch.setattr(runtime, "_emit_session_shutdown", no_op)
    monkeypatch.setattr(runtime, "_emit_session_start", no_op)
    monkeypatch.setattr(runtime, "_run_with_session", no_op)
    monkeypatch.setattr(runtime, "_reinit_after_context_create", lambda: None)

    asyncio.run(runtime.new_session())

    assert agent.aborted is True
    assert idle_at_rebuild == [True]


class _TurnAgent:
    """An agent stuck mid-turn until abort() releases it."""

    def __init__(self) -> None:
        self.aborted = False
        self._idle = asyncio.Event()

    def is_idle(self) -> bool:
        return self._idle.is_set()

    def abort(self) -> None:
        self.aborted = True
        self._idle.set()

    async def wait_for_idle(self) -> None:
        await self._idle.wait()


def _settle_runtime(agent: Any, callback_depth: int = 0) -> Runtime:
    runtime = object.__new__(Runtime)
    runtime._context = SimpleNamespace(agent=agent)  # type: ignore[assignment]
    runtime._extension_callback_depth = callback_depth
    return runtime


def test_settle_active_turn_aborts_and_drains_before_a_session_switch() -> None:
    """A turn still in flight would go on writing into the session being
    replaced, so it is stopped and drained first."""
    agent = _TurnAgent()
    runtime = _settle_runtime(agent)

    asyncio.run(runtime._settle_active_turn())

    assert agent.aborted is True
    assert agent.is_idle() is True


def test_settle_active_turn_is_a_no_op_when_already_idle() -> None:
    agent = _TurnAgent()
    agent.abort()  # reach idle without recording an abort from the switch
    agent.aborted = False
    runtime = _settle_runtime(agent)

    asyncio.run(runtime._settle_active_turn())

    assert agent.aborted is False


def test_settle_active_turn_does_not_wait_inside_an_extension_callback() -> None:
    """The callback may itself be running inside the turn being waited for,
    so waiting there would deadlock. The abort is still requested."""

    class _NeverIdleAgent(_TurnAgent):
        def abort(self) -> None:
            self.aborted = True  # deliberately never becomes idle

    agent = _NeverIdleAgent()
    runtime = _settle_runtime(agent, callback_depth=1)

    asyncio.run(asyncio.wait_for(runtime._settle_active_turn(), timeout=2))

    assert agent.aborted is True
    assert agent.is_idle() is False


def test_settle_active_turn_gives_up_rather_than_hanging_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn that never reports itself idle must not block the switch forever."""
    import tau.runtime.service as runtime_service

    class _NeverIdleAgent(_TurnAgent):
        def abort(self) -> None:
            self.aborted = True

    monkeypatch.setattr(runtime_service, "_SESSION_SETTLE_TIMEOUT", 0.01)
    agent = _NeverIdleAgent()
    runtime = _settle_runtime(agent)

    asyncio.run(asyncio.wait_for(runtime._settle_active_turn(), timeout=2))

    assert agent.aborted is True


def test_settle_active_turn_waits_out_a_compaction_started_outside_a_turn() -> None:
    """wait_for_idle() only tracks the invoke() lifecycle: a manual compaction
    moves the phase without clearing the idle event, so waiting on the event
    alone returns instantly and the session gets swapped mid-compaction."""

    class _CompactingAgent(_TurnAgent):
        def __init__(self) -> None:
            super().__init__()
            self._idle.set()  # as it stands after any completed turn
            self.compacting = True

        def is_idle(self) -> bool:
            return not self.compacting

    async def scenario() -> None:
        agent = _CompactingAgent()
        runtime = _settle_runtime(agent)

        settle = asyncio.ensure_future(runtime._settle_active_turn())
        await asyncio.sleep(0.05)
        assert not settle.done(), "returned while the agent was still compacting"

        agent.compacting = False
        await asyncio.wait_for(settle, timeout=2)

    asyncio.run(scenario())


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
    counts = {
        "settings": 0,
        "llm": 0,
        "session": 0,
        "hooks": 0,
        "registry": 0,
        "inline": 0,
    }

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

    def inline_factory(tau: ExtensionAPI) -> None:
        counts["inline"] += 1
        tau.append_prompt("inline")

    config = RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        persist_session=False,
        project_trusted=True,
        extension_factories=[inline_factory],
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
    assert context.ext_runtime is not None
    assert context.ext_runtime.get_prompt_appends()[-1] == "inline"
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
        "inline": 1,
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


def test_runtime_config_base_url_overrides_llm_options(tmp_path: Path) -> None:
    """RuntimeConfig.base_url must apply as a temporary override (not persisted)."""
    config = RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        provider="groq",
        model_id="llama-3.3-70b-versatile",
        base_url="http://custom-gateway.example/v1",
        project_trusted=True,
        persist_session=False,
    )

    context = asyncio.run(RuntimeContext.create(config))
    assert context.settings_manager is not None
    asyncio.run(context.settings_manager.flush())

    assert context.llm.api.options.base_url == "http://custom-gateway.example/v1"
    # Nothing persisted: settings.json (if written at all) must not mention the override.
    settings_file = tmp_path / "config" / "settings.json"
    if settings_file.exists():
        assert "custom-gateway.example" not in settings_file.read_text()


def test_runtime_config_allows_media_without_text(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path, persist=False)
    config = RuntimeConfig(cwd=tmp_path, initial_images=[b"\x89PNG\r\n\x1a\n"])

    _seed_initial_messages(manager, config)

    message = manager.build_session_context().messages[0]
    assert isinstance(message, UserMessage)
    assert isinstance(message.contents[0], TextContent)
    assert message.contents[0].content == ""
    assert isinstance(message.contents[1], ImageContent)


def test_create_with_result_collects_startup_outcome(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = object.__new__(Runtime)
    diagnostic = ResourceDiagnostic(
        severity="warning",
        message="missing skill",
        path=tmp_path / "skill",
    )
    extension_error = ExtensionError(
        extension_path="inline:broken:0",
        event="load",
        error="failed",
    )
    runtime._context = SimpleNamespace(
        llm=SimpleNamespace(
            model=SimpleNamespace(id="selected-model"),
            provider_id="selected-provider",
            fallback_reason="requested provider unavailable",
        ),
        requested_model_id="requested-model",
        requested_provider_id="requested-provider",
        resource_snapshot=ResourceSnapshot(
            builtins_extension_dir=tmp_path,
            diagnostics=(diagnostic,),
        ),
        ext_runtime=SimpleNamespace(errors=[extension_error]),
    )

    async def fake_create(cls, _config):
        return runtime

    monkeypatch.setattr(Runtime, "create", classmethod(fake_create))
    result = asyncio.run(Runtime.create_with_result(RuntimeConfig(cwd=tmp_path)))

    assert isinstance(result, RuntimeStartupResult)
    assert result.runtime is runtime
    assert result.resource_diagnostics == (diagnostic,)
    assert result.extension_errors == (extension_error,)
    assert result.requested_model_id == "requested-model"
    assert result.selected_model_id == "selected-model"
    assert result.selected_provider_id == "selected-provider"
    assert result.model_fallback_reason == "requested provider unavailable"
    assert result.has_issues


class _FakeAsyncApi:
    """Duck-typed stand-in for LazyAPI's aclose() contract."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.aclose_calls = 0
        self.options = SimpleNamespace(thinking_level=None, distrust_thought_signatures=False)

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _FakeLLM:
    def __init__(self, name: str, provider_id: str = "test-provider") -> None:
        self.model = SimpleNamespace(id=name, thinking=False, input_limit=100_000)
        self.api = _FakeAsyncApi(name)
        self.provider_id = provider_id


class _FakeEngine:
    def __init__(self, llm: _FakeLLM) -> None:
        self.llm = llm
        self._context_window = 0

    def set_llm(self, llm: _FakeLLM) -> None:
        self.llm = llm


def test_set_model_closes_outgoing_client_on_successful_swap() -> None:
    """Runtime.set_model() must close the provider client it's replacing.

    Without this, every model switch during a live session abandons the
    outgoing provider's connection pool — nothing else holds a reference to
    it once agent._engine.set_llm() overwrites .llm, and there's no other
    lifecycle hook that would close it.
    """
    old_llm = _FakeLLM("old-model")
    new_llm = _FakeLLM("new-model")
    engine = _FakeEngine(old_llm)
    agent = SimpleNamespace(_engine=engine)

    runtime = object.__new__(Runtime)
    runtime._config = RuntimeConfig(
        cwd=Path.cwd(),
        dependencies=RuntimeDependencies(llm=lambda ctx: new_llm),  # type: ignore[arg-type]
    )
    runtime._context = SimpleNamespace(
        agent=agent,
        hooks=Hooks(),
        settings_manager=SimpleNamespace(
            get_thinking_level=lambda: None,
            set_model_ref=lambda *a, **k: None,
        ),
        session_manager=None,
    )

    ok = asyncio.run(runtime.set_model("new-model", "test-provider"))

    assert ok is True
    assert engine.llm is new_llm
    assert old_llm.api.aclose_calls == 1
    assert new_llm.api.aclose_calls == 0


def test_set_model_never_closes_client_that_was_never_used() -> None:
    """A model switched away from without ever sending a message must not
    force-construct its client just to close it (see LazyAPI.aclose()).
    """
    calls: list[str] = []

    class _NeverResolvedApi:
        def __getattr__(self, name: str):  # any real access means unwanted resolution
            calls.append(name)
            raise AssertionError(f"unexpected attribute access: {name}")

        async def aclose(self) -> None:
            calls.append("aclose")

    old_llm = SimpleNamespace(
        model=SimpleNamespace(id="old-model"),
        api=_NeverResolvedApi(),
    )
    new_llm = _FakeLLM("new-model")
    engine = _FakeEngine(old_llm)  # type: ignore[arg-type]
    agent = SimpleNamespace(_engine=engine)

    runtime = object.__new__(Runtime)
    runtime._config = RuntimeConfig(
        cwd=Path.cwd(),
        dependencies=RuntimeDependencies(llm=lambda ctx: new_llm),  # type: ignore[arg-type]
    )
    runtime._context = SimpleNamespace(
        agent=agent,
        hooks=Hooks(),
        settings_manager=SimpleNamespace(
            get_thinking_level=lambda: None,
            set_model_ref=lambda *a, **k: None,
        ),
        session_manager=None,
    )

    ok = asyncio.run(runtime.set_model("new-model", "test-provider"))

    assert ok is True
    assert calls == ["aclose"]  # only aclose() itself was touched, nothing else


def test_set_model_close_failure_does_not_undo_a_successful_swap() -> None:
    """A close() error on the outgoing client must be logged and swallowed —
    the swap already succeeded and must not be reported as failed because of it.
    """

    class _FailsToClose:
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    old_llm = SimpleNamespace(model=SimpleNamespace(id="old-model"), api=_FailsToClose())
    new_llm = _FakeLLM("new-model")
    engine = _FakeEngine(old_llm)  # type: ignore[arg-type]
    agent = SimpleNamespace(_engine=engine)

    runtime = object.__new__(Runtime)
    runtime._config = RuntimeConfig(
        cwd=Path.cwd(),
        dependencies=RuntimeDependencies(llm=lambda ctx: new_llm),  # type: ignore[arg-type]
    )
    runtime._context = SimpleNamespace(
        agent=agent,
        hooks=Hooks(),
        settings_manager=SimpleNamespace(
            get_thinking_level=lambda: None,
            set_model_ref=lambda *a, **k: None,
        ),
        session_manager=None,
    )

    ok = asyncio.run(runtime.set_model("new-model", "test-provider"))

    assert ok is True
    assert engine.llm is new_llm


def test_emit_to_extension_times_out_a_hung_handler(monkeypatch) -> None:
    """A handler with no timeout of its own (unbounded network call, deadlock)
    must not hang _emit_to_extension() forever.

    This runs under Runtime._reload_lock during every enable/disable/reload —
    an unbounded hang here would wedge every future reload/toggle for the
    rest of the session, not just fail the current one.
    """
    import tau.runtime.service as runtime_service

    monkeypatch.setattr(runtime_service, "_SHUTDOWN_HOOK_TIMEOUT", 0.05)

    handler_started = asyncio.Event()

    async def _hangs_forever(_event, _ctx) -> None:
        handler_started.set()
        await asyncio.Event().wait()  # never set — simulates a truly hung handler

    ext = Extension(path="fake_ext", handlers={"extension_unload": [_hangs_forever]})

    runtime = object.__new__(Runtime)
    runtime._extension_callback_depth = 0
    runtime._extension_callbacks_idle = asyncio.Event()
    runtime._extension_callbacks_idle.set()
    runtime._context = SimpleNamespace(
        agent=None,
        session_manager=None,
        settings_manager=None,
    )

    async def _run() -> None:
        await asyncio.wait_for(
            runtime._emit_to_extension(ext, "extension_unload"),
            timeout=2.0,  # generous outer bound — fails loudly if the fix regresses
        )

    asyncio.run(_run())

    assert handler_started.is_set()
    # The timeout path must also leave the callback-depth counter balanced,
    # same as a normal exception — otherwise _extension_callbacks_idle never
    # gets set again and the deferred-reload drain hangs on *that* instead.
    assert runtime._extension_callback_depth == 0
    assert runtime._extension_callbacks_idle.is_set()


class _NamingSessionManager:
    """Session manager double that records names the way the real one does."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.entry_ids = iter(["entry-1", "entry-2", "entry-3"])

    def append_session_info(self, name: str) -> str:
        self.names.append(name)
        return next(self.entry_ids)

    def get_session_name(self) -> str | None:
        return self.names[-1] if self.names else None


def _naming_runtime() -> tuple[Runtime, _NamingSessionManager, Hooks]:
    runtime = object.__new__(Runtime)
    sm = _NamingSessionManager()
    hooks = Hooks()
    runtime._context = SimpleNamespace(hooks=hooks, session_manager=sm)  # type: ignore[assignment]
    return runtime, sm, hooks


def test_set_session_name_announces_the_change() -> None:
    runtime, sm, hooks = _naming_runtime()
    seen: list[Any] = []
    hooks.register("session_info_changed", lambda event: seen.append(event))

    entry_id = asyncio.run(runtime.set_session_name("refactor rpc"))

    assert sm.names == ["refactor rpc"]
    assert entry_id == "entry-1"
    assert len(seen) == 1
    assert seen[0].name == "refactor rpc"
    assert seen[0].entry_id == "entry-1"
    # Nothing to report as the previous name the first time a session is named.
    assert seen[0].previous_name is None


def test_rename_reports_the_name_it_replaced() -> None:
    runtime, _sm, hooks = _naming_runtime()
    seen: list[Any] = []
    hooks.register("session_info_changed", lambda event: seen.append(event))

    asyncio.run(runtime.set_session_name("first"))
    asyncio.run(runtime.set_session_name("second"))

    assert [(e.previous_name, e.name) for e in seen] == [(None, "first"), ("first", "second")]


def test_set_session_name_without_a_session_is_a_no_op() -> None:
    runtime = object.__new__(Runtime)
    hooks = Hooks()
    runtime._context = SimpleNamespace(hooks=hooks, session_manager=None)  # type: ignore[assignment]
    seen: list[Any] = []
    hooks.register("session_info_changed", lambda event: seen.append(event))

    assert asyncio.run(runtime.set_session_name("nowhere")) is None
    assert seen == [], "an ephemeral run has no name to change"
