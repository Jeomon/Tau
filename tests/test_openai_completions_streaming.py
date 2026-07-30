"""Tests for openai_completions.py / github_copilot_chat.py stream()
robustness against non-standard OpenAI-compatible providers — regression
coverage for two bugs found in a related project: ``delta.content``
arriving as a list of typed content-part dicts instead of a plain string
(some Databricks-hosted models), and a stream that never sends
``finish_reason`` at all. Both providers share the same underlying bug shape
(github_copilot_chat.py is a near-verbatim parallel implementation, not
shared code), so both are covered here rather than just the first one fixed.
"""

from __future__ import annotations

import httpx
import pytest

from tau.inference.api.text.github_copilot_chat import GitHubCopilotChatAPI
from tau.inference.api.text.openai_completions import OpenAICompletionsAPI
from tau.inference.api.text.utils import extract_openai_delta_text
from tau.inference.model.types import Model
from tau.inference.types import (
    EndEvent,
    LLMContext,
    LLMOptions,
    StopReason,
    TextDeltaEvent,
    TextEndEvent,
    ToolCallEndEvent,
)
from tau.message.types import UserMessage

_MODEL = Model(id="gpt-x", name="gpt-x", provider="openai")

_PROVIDERS = [OpenAICompletionsAPI, GitHubCopilotChatAPI]
_PROVIDER_IDS = ["openai_completions", "github_copilot_chat"]


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Replays a canned SSE body — same pattern as
    test_provider_raw_response_hook.py's transport."""

    def __init__(self, body: bytes, status_code: int = 200):
        self.body = body
        self.status_code = status_code

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self.status_code,
            headers={"content-type": "text/event-stream"},
            content=self.body,
        )


def _api(api_cls: type, body: bytes):
    options = LLMOptions(api_key="sk-test", headers={})
    api = api_cls(options)
    api._client._client = httpx.AsyncClient(transport=_CapturingTransport(body))
    return api


class TestExtractOpenaiDeltaText:
    """Unit tests for the shared normalizer (tau/inference/api/text/utils.py) —
    used by every "openai_completions"-family provider."""

    def test_plain_string_passes_through(self):
        assert extract_openai_delta_text("hello") == "hello"

    def test_none_is_empty(self):
        assert extract_openai_delta_text(None) == ""

    def test_list_of_typed_parts_keeps_only_text(self):
        content = [
            {"type": "reasoning", "summary": ["thinking..."]},
            {"type": "text", "text": "Hello!"},
        ]
        assert extract_openai_delta_text(content) == "Hello!"

    def test_list_with_multiple_text_parts_concatenates(self):
        content = [{"type": "text", "text": "Hello, "}, {"type": "text", "text": "world!"}]
        assert extract_openai_delta_text(content) == "Hello, world!"

    def test_list_with_no_text_parts_is_empty(self):
        assert extract_openai_delta_text([{"type": "reasoning", "summary": []}]) == ""

    def test_unrecognised_type_is_empty(self):
        assert extract_openai_delta_text(42) == ""


@pytest.mark.parametrize("api_cls", _PROVIDERS, ids=_PROVIDER_IDS)
class TestArrayContentStreaming:
    @pytest.mark.asyncio
    async def test_array_content_extracts_text_not_garbage(self, api_cls):
        """Before the fix, `text_buf += delta.content` on a list would raise
        (Python) — the bug's JS analogue produced literal "[object Object]"
        text. Either way, the fix must yield clean extracted text."""
        sse = (
            b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
            b'"choices":[{"index":0,"delta":{"role":"assistant","content":'
            b'[{"type":"reasoning","summary":["..."]},{"type":"text","text":"Hello!"}]},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
            b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
            b"data: [DONE]\n\n"
        )
        api = _api(api_cls, sse)
        ctx = LLMContext(messages=[UserMessage.from_text("hi")])
        events = [e async for e in api.stream(ctx, model=_MODEL)]

        deltas = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert deltas and deltas[0].text.content == "Hello!"
        text_ends = [e for e in events if isinstance(e, TextEndEvent)]
        assert text_ends and text_ends[0].text.content == "Hello!"
        # No stray "[object Object]"-style corruption anywhere in the stream.
        assert not any("object" in e.text.content.lower() for e in deltas)


@pytest.mark.parametrize("api_cls", _PROVIDERS, ids=_PROVIDER_IDS)
class TestMissingFinishReason:
    @pytest.mark.asyncio
    async def test_stream_without_finish_reason_does_not_raise(self, api_cls):
        """Some non-standard providers never send finish_reason at all —
        this must degrade gracefully (implicit stop), not crash the turn."""
        sse = (
            b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
            b'"choices":[{"index":0,"delta":{"role":"assistant","content":"Hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        api = _api(api_cls, sse)
        ctx = LLMContext(messages=[UserMessage.from_text("hi")])
        events = [e async for e in api.stream(ctx, model=_MODEL)]

        ends = [e for e in events if isinstance(e, EndEvent)]
        assert len(ends) == 1
        assert ends[0].reason == StopReason.Stop

        text_ends = [e for e in events if isinstance(e, TextEndEvent)]
        assert text_ends and text_ends[0].text.content == "Hi"

    @pytest.mark.asyncio
    async def test_open_tool_call_is_still_resolved_without_finish_reason(self, api_cls):
        """An in-progress tool call must still get a ToolCallEndEvent, not be
        left dangling, even when the stream never sends finish_reason."""
        sse = (
            b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-x",'
            b'"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":'
            b'[{"index":0,"id":"call_1","type":"function",'
            b'"function":{"name":"read_file","arguments":"{\\"path\\":\\"a.py\\"}"}}]}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        api = _api(api_cls, sse)
        ctx = LLMContext(messages=[UserMessage.from_text("hi")])
        events = [e async for e in api.stream(ctx, model=_MODEL)]

        tool_ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(tool_ends) == 1
        assert tool_ends[0].tool_call.id == "call_1"
        assert tool_ends[0].tool_call.name == "read_file"
        assert tool_ends[0].tool_call.args == {"path": "a.py"}
