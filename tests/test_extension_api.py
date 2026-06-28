"""Tests for the extension API additions: programmatic model switch, custom
providers (OAuth + custom transport + auth_header), and deep tool introspection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.extensions.api import ExtensionAPI, _RuntimeRef
from tau.extensions.context import ExtensionContext
from tau.inference.api.text.base import BaseLLMAPI
from tau.inference.api.text.service import TextLLM
from tau.inference.provider.types import APIProvider, AuthType, OAuthProvider


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
