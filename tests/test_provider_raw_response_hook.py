"""End-to-end coverage for the before_provider_request headers path and the
on_response raw-status/headers callback, against the real Anthropic/OpenAI
SDKs over a mocked httpx transport (no network).

Exercises exactly what an extension hook does: mutate `options.headers` in
place before the call, and read status/headers via `options.on_response`
after — verifying both survive the swap from the SDKs' convenience
`.stream()`/`.create(stream=True)` helpers to `.with_streaming_response`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tau.inference.api.text.anthropic_claude_code import AnthropicClaudeCodeAPI
from tau.inference.api.text.anthropic_messages import AnthropicMessagesAPI
from tau.inference.api.text.gemini_generate import GeminiGenerateAPI
from tau.inference.api.text.github_copilot_chat import GitHubCopilotChatAPI
from tau.inference.api.text.mistral_chat import MistralChatAPI
from tau.inference.api.text.ollama_chat import OllamaChatAPI
from tau.inference.api.text.openai_completions import OpenAICompletionsAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.model.types import Model
from tau.inference.types import LLMContext, LLMOptions
from tau.message.types import UserMessage

_ANTHROPIC_MODEL = Model(id="claude-x", name="claude-x", provider="anthropic")
_OPENAI_MODEL = Model(id="gpt-x", name="gpt-x", provider="openai")

_ANTHROPIC_SSE = b"""event: message_start
data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-x","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":5}}

event: message_stop
data: {"type":"message_stop"}

"""

_OPENAI_SSE = (
    b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
    b'"choices":[{"index":0,"delta":{"role":"assistant","content":"Hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
    b"data: [DONE]\n\n"
)


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Records the last request's headers, replays a canned SSE body."""

    def __init__(self, body: bytes, status_code: int = 200, extra_headers: dict | None = None):
        self.body = body
        self.status_code = status_code
        self.extra_headers = extra_headers or {}
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(
            self.status_code,
            headers={"content-type": "text/event-stream", **self.extra_headers},
            content=self.body,
        )


@pytest.mark.asyncio
async def test_anthropic_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_ANTHROPIC_SSE, extra_headers={"x-trace-id": "abc123"})
    options = LLMOptions(api_key="sk-test", headers={})
    api = AnthropicMessagesAPI(options)
    # Swap in the mocked transport post-construction (same pattern as
    # test_anthropic_tool_history.py's _api() helper) rather than reaching
    # into AsyncAnthropic's constructor kwargs.
    api._client._client = httpx.AsyncClient(transport=transport)

    captured: list[APIResponse] = []
    options.on_response = captured.append

    # Simulate a `before_provider_request` extension hook mutating the live
    # headers dict right before the request goes out.
    options.headers["X-Tracing-Session"] = "session-42"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    events = [e async for e in api.stream(ctx, model=_ANTHROPIC_MODEL)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-42"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "abc123"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert texts and texts[0].text.content == "Hello"


@pytest.mark.asyncio
async def test_openai_completions_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_OPENAI_SSE, extra_headers={"x-trace-id": "xyz789"})
    options = LLMOptions(api_key="sk-test", headers={})
    api = OpenAICompletionsAPI(options)
    api._client._client = httpx.AsyncClient(transport=transport)

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-99"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    events = [e async for e in api.stream(ctx, model=_OPENAI_MODEL)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-99"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "xyz789"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert any(e.text.content == "Hi" for e in texts)


@pytest.mark.asyncio
async def test_anthropic_claude_code_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_ANTHROPIC_SSE, extra_headers={"x-trace-id": "cc123"})
    options = LLMOptions(api_key="sk-oauth-test", headers={})
    api = AnthropicClaudeCodeAPI(options)
    api._client._client = httpx.AsyncClient(transport=transport)

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-cc"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    events = [e async for e in api.stream(ctx, model=_ANTHROPIC_MODEL)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-cc"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "cc123"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert texts and texts[0].text.content == "Hello"


_OLLAMA_NDJSON = (
    b'{"model":"llama3","created_at":"t","message":{"role":"assistant","content":"Hi"},"done":false}\n'
    b'{"model":"llama3","created_at":"t","message":{"role":"assistant","content":""},"done":true,'
    b'"done_reason":"stop","prompt_eval_count":5,"eval_count":2}\n'
)

_MISTRAL_SSE = (
    b'data: {"id":"c1","model":"mistral-x","choices":[{"index":0,"delta":{"role":"assistant",'
    b'"content":"Hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","model":"mistral-x","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
    b"data: [DONE]\n\n"
)

_GEMINI_MODEL = Model(id="gemini-x", name="gemini-x", provider="google")


def _gemini_sse_body() -> bytes:
    chunks = [
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Hi"}]}, "index": 0}]},
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": ""}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
        },
    ]
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)


@pytest.mark.asyncio
async def test_ollama_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_OLLAMA_NDJSON, extra_headers={"x-trace-id": "oll1"})
    options = LLMOptions(headers={})
    api = OllamaChatAPI(options)
    api._client._client = httpx.AsyncClient(
        transport=transport, base_url="http://localhost:11434"
    )

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-ollama"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    model = Model(id="llama3", name="llama3", provider="ollama")
    events = [e async for e in api.stream(ctx, model=model)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-ollama"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "oll1"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert texts and texts[0].text.content == "Hi"


@pytest.mark.asyncio
async def test_mistral_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_MISTRAL_SSE, extra_headers={"x-trace-id": "mi1"})
    mock_client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    options = LLMOptions(api_key="k", headers={})
    api = MistralChatAPI(options)
    api._client = api._client.__class__(api_key="k", async_client=mock_client)

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-mistral"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    model = Model(id="mistral-x", name="mistral-x", provider="mistral")
    events = [e async for e in api.stream(ctx, model=model)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-mistral"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "mi1"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert texts and texts[0].text.content == "Hi"


@pytest.mark.asyncio
async def test_gemini_headers_hook_and_raw_response_capture():
    from google import genai
    from google.genai import types as genai_types

    transport = _CapturingTransport(
        _gemini_sse_body(), extra_headers={"x-trace-id": "gem1", "content-type": "text/event-stream"}
    )
    mock_client = httpx.AsyncClient(transport=transport)
    options = LLMOptions(api_key="k", headers={})
    api = GeminiGenerateAPI(options)
    api._client = genai.Client(
        api_key="k", http_options=genai_types.HttpOptions(httpx_async_client=mock_client)
    )

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-gemini"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    events = [e async for e in api.stream(ctx, model=_GEMINI_MODEL)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-gemini"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "gem1"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert texts and texts[0].text.content == "Hi"


@pytest.mark.asyncio
async def test_github_copilot_chat_headers_hook_and_raw_response_capture():
    transport = _CapturingTransport(_OPENAI_SSE, extra_headers={"x-trace-id": "gh456"})
    options = LLMOptions(api_key="sk-test", headers={})
    api = GitHubCopilotChatAPI(options)
    api._client._client = httpx.AsyncClient(transport=transport)

    captured: list[APIResponse] = []
    options.on_response = captured.append
    options.headers["X-Tracing-Session"] = "session-gh"

    ctx = LLMContext(messages=[UserMessage.from_text("hi")])
    events = [e async for e in api.stream(ctx, model=_OPENAI_MODEL)]

    sent = transport.last_request
    assert sent is not None
    assert sent.headers["x-tracing-session"] == "session-gh"
    # Static Copilot identity headers still merged in from construction time.
    assert sent.headers["editor-version"] == "vscode/1.107.0"

    assert len(captured) == 1
    assert captured[0].status_code == 200
    assert captured[0].headers.get("x-trace-id") == "gh456"

    texts = [e for e in events if type(e).__name__ == "TextDeltaEvent"]
    assert any(e.text.content == "Hi" for e in texts)
