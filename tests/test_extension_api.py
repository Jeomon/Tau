"""Tests for the extension API additions: programmatic model switch, custom
providers (OAuth + custom transport + auth_header), and deep tool introspection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.extensions.api import ExtensionAPI, ShortcutRegistration, _RuntimeRef
from tau.extensions.context import ExtensionContext
from tau.extensions.loader import load_inline_extensions
from tau.extensions.runtime import ExtensionRuntime
from tau.hooks.runtime import RuntimeReadyEvent
from tau.hooks.service import Hooks
from tau.inference.api.text.base import BaseLLMAPI
from tau.inference.api.text.service import TextLLM
from tau.inference.provider.types import APIProvider, AuthType, OAuthProvider
from tau.modes.interactive.app import _resolve_extension_shortcuts
from tau.tui.input import configure_keybindings


def _make_api(runtime_ref: _RuntimeRef | None = None) -> ExtensionAPI:
    """Construct an ExtensionAPI with stub llm/settings for registration tests."""
    from tau.extensions.api import Extension

    return ExtensionAPI(
        extension=Extension(path="test"),
        llm=SimpleNamespace(model=SimpleNamespace(id="x"), provider_id="x"),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cwd=Path("."),
        runtime_ref=runtime_ref,
    )


def test_load_inline_extensions_runs_sync_and_async_factories(tmp_path: Path) -> None:
    runtime_ref = _RuntimeRef()

    def first(tau: ExtensionAPI) -> None:
        tau.append_prompt("first")
        tau.provide("value", 42)

    async def second(tau: ExtensionAPI) -> None:
        tau.append_prompt(f"second:{tau.get_service('value')}")

    result = asyncio.run(
        load_inline_extensions(
            [first, second],
            llm=SimpleNamespace(model=SimpleNamespace(id="x"), provider_id="x"),  # type: ignore[arg-type]
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            cwd=tmp_path,
            runtime_ref=runtime_ref,
        )
    )

    assert result.errors == []
    assert [extension.source for extension in result.extensions] == ["inline", "inline"]
    assert [extension.prompt_appends for extension in result.extensions] == [
        ["first"],
        ["second:42"],
    ]


def test_load_inline_extensions_isolates_factory_errors(tmp_path: Path) -> None:
    def broken(_tau: ExtensionAPI) -> None:
        raise RuntimeError("factory failed")

    def healthy(tau: ExtensionAPI) -> None:
        tau.append_prompt("loaded")

    result = asyncio.run(
        load_inline_extensions(
            [broken, healthy],
            llm=SimpleNamespace(model=SimpleNamespace(id="x"), provider_id="x"),  # type: ignore[arg-type]
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            cwd=tmp_path,
            runtime_ref=_RuntimeRef(),
        )
    )

    assert len(result.errors) == 1
    assert result.errors[0].extension_path == "inline:broken:0"
    assert result.errors[0].error == "RuntimeError: factory failed"
    assert [extension.prompt_appends for extension in result.extensions] == [["loaded"]]


def test_inline_extension_reload_does_not_duplicate_handlers(tmp_path: Path) -> None:
    calls = 0
    hooks = Hooks()
    runtime_ref = _RuntimeRef()

    def factory(tau: ExtensionAPI) -> None:
        @tau.on("runtime_ready")
        def on_ready(_event: object, _context: object) -> None:
            nonlocal calls
            calls += 1

    def load() -> ExtensionRuntime:
        result = asyncio.run(
            load_inline_extensions(
                [factory],
                llm=SimpleNamespace(model=SimpleNamespace(id="x"), provider_id="x"),  # type: ignore[arg-type]
                settings=SimpleNamespace(),  # type: ignore[arg-type]
                cwd=tmp_path,
                runtime_ref=runtime_ref,
            )
        )
        return ExtensionRuntime(result, hooks, runtime_ref)

    first = load()
    asyncio.run(hooks.emit(RuntimeReadyEvent()))
    first.unsubscribe()

    second = load()
    asyncio.run(hooks.emit(RuntimeReadyEvent()))
    second.unsubscribe()

    assert calls == 2


def test_register_shortcut_records_literal_key_and_source() -> None:
    api = _make_api()

    @api.register_shortcut("ctrl+g", "Open greeter")
    async def open_greeter(_ctx: ExtensionContext) -> None:
        pass

    shortcut = api._extension.shortcuts[0]
    assert shortcut.key == "ctrl+g"
    assert shortcut.description == "Open greeter"
    assert shortcut.handler is open_greeter
    assert shortcut.extension_path == "test"


def _shortcut(key: str, path: str) -> ShortcutRegistration:
    return ShortcutRegistration(key, None, lambda _ctx: None, path)


def test_reserved_tui_shortcut_cannot_be_replaced() -> None:
    configure_keybindings({})

    resolved, warnings = _resolve_extension_shortcuts([_shortcut("ctrl+c", "extension.py")])

    assert resolved == []
    assert warnings == [
        "Extension shortcut 'ctrl+c' from extension.py conflicts with reserved "
        "TUI action tui.app.abort; skipping."
    ]


def test_non_reserved_tui_shortcut_can_be_replaced() -> None:
    configure_keybindings({})
    shortcut = _shortcut("ctrl+o", "extension.py")

    resolved, warnings = _resolve_extension_shortcuts([shortcut])

    assert resolved == [shortcut]
    assert warnings == [
        "Extension shortcut 'ctrl+o' from extension.py overrides TUI action app.details.toggle."
    ]


def test_last_extension_shortcut_wins() -> None:
    configure_keybindings({})
    first = _shortcut("ctrl+shift+x", "first.py")
    second = _shortcut("shift+ctrl+x", "second.py")

    resolved, warnings = _resolve_extension_shortcuts([first, second])

    assert resolved == [second]
    assert warnings == [
        "Extension shortcut 'shift+ctrl+x' is registered by both first.py and "
        "second.py; using second.py."
    ]


def test_conflicts_use_effective_user_keymap() -> None:
    configure_keybindings({"tui.app.abort": ["ctrl+x"]})

    resolved, warnings = _resolve_extension_shortcuts([_shortcut("ctrl+x", "extension.py")])

    assert resolved == []
    assert warnings == [
        "Extension shortcut 'ctrl+x' from extension.py conflicts with reserved "
        "TUI action tui.app.abort; skipping."
    ]


# ── Custom providers ──────────────────────────────────────────────────────────


def test_register_provider_oauth():
    api = _make_api()

    async def _login(_callbacks):  # pragma: no cover - not invoked here
        raise NotImplementedError

    try:
        api.register_provider(
            "my-oauth",
            {
                "name": "My OAuth",
                "api": "anthropic_messages",
                "oauth": {"name": "My OAuth (SSO)", "login": _login},
                "models": [{"id": "m1", "context_window": 1000}],
            },
        )
        provider = TextLLM._builtin_providers().get("my-oauth")
        assert isinstance(provider, OAuthProvider)
        assert provider.auth_type == AuthType.OAuth
        assert provider.name == "My OAuth (SSO)"
        # model registered against the provider
        assert TextLLM._builtin_models().get("m1") is not None
    finally:
        api.unregister_provider("my-oauth")
        assert TextLLM._builtin_providers().get("my-oauth") is None


def test_register_provider_custom_stream():
    api = _make_api()

    async def _stream(_context, _model, _options):  # pragma: no cover - not invoked
        if False:
            yield None

    try:
        api.register_provider(
            "my-stream",
            {"name": "My Stream", "stream": _stream, "models": [{"id": "s1"}]},
        )
        provider = TextLLM._builtin_providers().get("my-stream")
        assert isinstance(provider, APIProvider)
        # api was replaced with a generated BaseLLMAPI subclass
        assert isinstance(provider.api, type) and issubclass(provider.api, BaseLLMAPI)
    finally:
        api.unregister_provider("my-stream")


def test_register_provider_auth_header():
    api = _make_api()
    try:
        api.register_provider(
            "my-keyed",
            {
                "name": "Keyed",
                "api": "openai_completions",
                "api_key": "sk-test",
                "auth_header": True,
            },
        )
        provider = TextLLM._builtin_providers().get("my-keyed")
        assert isinstance(provider, APIProvider)
        headers = provider.options.headers
        assert headers is not None
        assert headers["Authorization"] == "Bearer sk-test"
    finally:
        api.unregister_provider("my-keyed")


# ── Deep tool introspection ───────────────────────────────────────────────────


def test_get_all_tools_includes_schema_and_guidelines():
    from pydantic import BaseModel

    class _Schema(BaseModel):
        path: str

    tool = SimpleNamespace(
        name="reader",
        description="reads",
        schema=_Schema,
        prompt_guidelines="be careful",
    )
    registry = SimpleNamespace(list=lambda: [tool])
    runtime = SimpleNamespace(_context=SimpleNamespace(tool_registry=registry))
    ref = _RuntimeRef()
    ref.runtime = runtime

    api = _make_api(ref)
    tools = api.get_all_tools()
    assert len(tools) == 1
    entry = tools[0]
    assert entry["name"] == "reader"
    assert entry["prompt_guidelines"] == "be careful"
    params = entry["parameters"]
    assert params is not None
    assert params["properties"]["path"]["type"] == "string"


# ── Programmatic model switch ─────────────────────────────────────────────────


def test_context_set_model_delegates_and_returns_bool():
    calls: list[tuple[str, str | None]] = []

    class _Runtime:
        async def set_model(self, model_id: str, provider: str | None = None) -> bool:
            calls.append((model_id, provider))
            return True

    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = _Runtime()  # type: ignore[attr-defined]

    ok = asyncio.run(ctx.set_model("claude-sonnet-4-6"))
    assert ok is True
    assert calls == [("claude-sonnet-4-6", None)]


def test_context_set_model_no_runtime_returns_false():
    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = None  # type: ignore[attr-defined]
    assert asyncio.run(ctx.set_model("x")) is False


def test_context_phase_reports_agent_lifecycle_state():
    from tau.agent.types import AgentPhase

    class _Agent:
        phase = AgentPhase.COMPACTION

    class _Runtime:
        agent = _Agent()

    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = _Runtime()  # type: ignore[attr-defined]

    assert ctx.phase is AgentPhase.COMPACTION
    assert ctx.is_idle() is False


def test_context_phase_defaults_to_idle_without_runtime():
    from tau.agent.types import AgentPhase

    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = None  # type: ignore[attr-defined]

    assert ctx.phase is AgentPhase.IDLE
    assert ctx.is_idle() is True


def test_send_user_message_trigger_turn_invokes_idle_runtime():
    calls: list[str] = []

    class _Agent:
        def is_idle(self) -> bool:
            return True

    class _Runtime:
        agent = _Agent()

        async def invoke(self, content: str, *, display: bool = False) -> None:
            calls.append(f"{content}:{display}")

    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = _Runtime()  # type: ignore[attr-defined]

    asyncio.run(ctx.send_user_message("peer message", trigger_turn=True))

    assert calls == ["peer message:True"]


def test_send_user_message_trigger_turn_queues_when_busy():
    queued: list[str] = []

    class _Engine:
        async def follow_up(self, message) -> None:
            queued.append(message.contents[0].content)

    class _Agent:
        _engine = _Engine()

        def is_idle(self) -> bool:
            return False

    class _Runtime:
        agent = _Agent()

    ctx = ExtensionContext.__new__(ExtensionContext)
    ctx._runtime = _Runtime()  # type: ignore[attr-defined]

    asyncio.run(
        ctx.send_user_message(
            "peer message",
            deliver_as="follow_up",
            trigger_turn=True,
        )
    )

    assert queued == ["peer message"]


def test_runtime_invoke_display_renders_user_message():
    from tau.runtime.service import Runtime

    rendered: list[str] = []
    invoked: list[str] = []
    render_requests: list[bool] = []

    class _Agent:
        async def invoke(self, content: str, _options=None) -> None:
            invoked.append(content)

    class _Hooks:
        async def emit(self, _event) -> list:
            return []

    class _Layout:
        _tui = SimpleNamespace(request_render=lambda: render_requests.append(True))

        def add_message(self, message) -> None:
            rendered.append(message.contents[0].content)

    runtime = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(agent=_Agent(), hooks=_Hooks())
    runtime._layout = _Layout()

    asyncio.run(runtime.invoke("peer request", display=True))

    assert rendered == ["peer request"]
    assert invoked == ["peer request"]
    assert render_requests == [True]


def test_api_set_model_schedules_runtime_call():
    calls: list[tuple[str, str | None]] = []

    class _Runtime:
        async def set_model(self, model_id: str, provider: str | None = None) -> bool:
            calls.append((model_id, provider))
            return True

    ref = _RuntimeRef()
    ref.runtime = _Runtime()
    api = _make_api(ref)

    async def _run():
        api.set_model("gpt-x", "openai")
        # let the scheduled task run
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert calls == [("gpt-x", "openai")]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
