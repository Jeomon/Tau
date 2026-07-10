"""Regression tests for GPT-5.6 "Responses Lite" support in the OpenAI Codex adapter.

Codex 0.144 marks gpt-5.6-sol/terra/luna as Responses Lite models: the backend
rejects the legacy Responses envelope for them with HTTP 404 "Model not found".
See https://github.com/anomalyco/opencode/pull/36143 for the upstream fix this
mirrors.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from tau.inference.api.text.openai_codex_responses import (
    _RESPONSES_LITE_HEADER,
    _RESPONSES_LITE_MODELS,
    OpenAICodexResponsesAPI,
    _apply_responses_lite,
    _build_headers,
    _uuid7,
)
from tau.inference.model.types import Cost, Model
from tau.inference.types import LLMContext, LLMOptions
from tau.message.types import UserMessage

_UUID7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _model(model_id: str) -> Model:
    return Model(id=model_id, name=model_id, provider="openai-codex", cost=Cost())


def test_uuid7_has_correct_version_and_variant_nibbles() -> None:
    for _ in range(20):
        assert _UUID7_RE.match(_uuid7())


def test_lite_models_are_exactly_the_gpt_5_6_family() -> None:
    assert _RESPONSES_LITE_MODELS == {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}


def test_apply_responses_lite_folds_tools_and_instructions_into_input() -> None:
    body: dict[str, Any] = {
        "model": "gpt-5.6-luna",
        "instructions": "Be concise.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "tools": [{"type": "function", "name": "noop", "description": "", "parameters": {}}],
        "reasoning": {"effort": "high", "summary": "auto"},
    }

    out = _apply_responses_lite(body, "ses_123")

    assert "tools" not in out
    assert "instructions" not in out
    assert out["input"][0] == {
        "type": "additional_tools",
        "role": "developer",
        "tools": [{"type": "function", "name": "noop", "description": "", "parameters": {}}],
    }
    assert out["input"][1] == {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": "Be concise."}],
    }
    assert out["input"][2] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert out["tool_choice"] == "auto"
    assert out["parallel_tool_calls"] is False
    assert out["prompt_cache_key"] == "ses_123"
    assert out["reasoning"] == {"effort": "high", "summary": "auto", "context": "all_turns"}


def test_apply_responses_lite_without_tools_or_instructions() -> None:
    body: dict[str, Any] = {
        "model": "gpt-5.6-luna",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "reasoning": {"effort": "medium", "summary": "auto"},
    }

    out = _apply_responses_lite(body, "ses_456")

    assert out["input"][0] == {"type": "additional_tools", "role": "developer", "tools": []}
    assert out["input"][1] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert len(out["input"]) == 2


def test_build_headers_only_adds_lite_fields_when_lite() -> None:
    plain = _build_headers("tok", "acct", "ses_1", websocket=False, lite=False)
    assert "version" not in plain
    assert "x-session-affinity" not in plain
    assert plain["session-id"] == "ses_1"

    lite_sse = _build_headers("tok", "acct", "ses_1", websocket=False, lite=True)
    assert lite_sse["version"] == "0.144.0"
    assert lite_sse["x-session-affinity"] == "ses_1"
    assert _RESPONSES_LITE_HEADER not in lite_sse  # HTTP path: no WS-only header

    lite_ws = _build_headers("tok", "acct", "ses_1", websocket=True, lite=True)
    assert lite_ws[_RESPONSES_LITE_HEADER] == "true"


def test_stream_applies_lite_transform_and_reuses_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_stream_sse(
        self: OpenAICodexResponsesAPI, body: dict[str, Any], headers: dict[str, str]
    ) -> AsyncGenerator[Any, None]:
        captured.append({"body": body, "headers": headers})
        for _ in ():
            yield  # unreachable; makes this an async generator

    monkeypatch.setattr(OpenAICodexResponsesAPI, "_stream_sse", fake_stream_sse)

    fake_jwt = (
        "eyJhbGciOiAibm9uZSJ9."
        "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOiB7ImNoYXRncHRfYWNjb3VudF9pZCI6ICJhY2N0LTEifX0."
    )
    api = OpenAICodexResponsesAPI(LLMOptions(api_key=fake_jwt))
    context = LLMContext(messages=[UserMessage.from_text("hi")])

    async def run_twice() -> None:
        async for _ in api.stream(context, _model("gpt-5.6-luna")):
            pass
        async for _ in api.stream(context, _model("gpt-5.6-luna")):
            pass

    import asyncio

    asyncio.run(run_twice())

    assert len(captured) == 2
    first_body, second_body = captured[0]["body"], captured[1]["body"]
    assert first_body["prompt_cache_key"] == second_body["prompt_cache_key"]
    assert _UUID7_RE.match(first_body["prompt_cache_key"])
    assert captured[0]["headers"]["version"] == "0.144.0"


def test_stream_skips_lite_transform_for_non_lite_models(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_stream_sse(
        self: OpenAICodexResponsesAPI, body: dict[str, Any], headers: dict[str, str]
    ) -> AsyncGenerator[Any, None]:
        captured.append({"body": body, "headers": headers})
        for _ in ():
            yield  # unreachable; makes this an async generator

    monkeypatch.setattr(OpenAICodexResponsesAPI, "_stream_sse", fake_stream_sse)

    fake_jwt = (
        "eyJhbGciOiAibm9uZSJ9."
        "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOiB7ImNoYXRncHRfYWNjb3VudF9pZCI6ICJhY2N0LTEifX0."
    )
    api = OpenAICodexResponsesAPI(LLMOptions(api_key=fake_jwt))
    context = LLMContext(messages=[UserMessage.from_text("hi")])

    import asyncio

    async def run() -> None:
        async for _ in api.stream(context, _model("gpt-5.5")):
            pass

    asyncio.run(run())

    body = captured[0]["body"]
    assert "instructions" in body
    assert "prompt_cache_key" not in body
    assert "version" not in captured[0]["headers"]
