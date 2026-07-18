"""Regression tests for the direct OpenAI Responses adapter's message
conversion and stream loop.

Covers two production bugs:

- Assistant-history text must be replayed as "output_text" — the Responses API
  rejects "input_text" under role assistant ("input_text" is only valid for
  user/system content), which 400'd every multi-turn conversation on turn two.
- The installed openai SDK (2.x) emits "response.completed" (and
  "response.incomplete") as the terminal stream event, not "response.done"
  (Realtime-only) — and the Response object carries status/incomplete_details,
  not stop_reason. EndEvent/usage/stop-reason were silently lost on every
  stream.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from tau.inference.api.text.openai_responses import (
    OpenAIResponsesAPI,
    _messages_to_input,
)
from tau.inference.model.types import Cost, Model
from tau.inference.types import (
    EndEvent,
    LLMContext,
    LLMOptions,
    StopReason,
    TextEndEvent,
    ThinkingEndEvent,
    ToolCallEndEvent,
)
from tau.message.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    UserMessage,
)

# ---------------------------------------------------------------------------
# _messages_to_input — assistant text must round-trip as output_text
# ---------------------------------------------------------------------------


def _text_types(item: dict[str, Any]) -> list[str]:
    return [part["type"] for part in item["content"] if "text" in part]


def test_assistant_history_text_is_output_text() -> None:
    _, items = _messages_to_input(
        [
            UserMessage.from_text("question"),
            AssistantMessage.from_text("answer"),
            UserMessage.from_text("follow-up"),
        ],
        supports_thinking=True,
    )

    assert [i["role"] for i in items] == ["user", "assistant", "user"]
    assert _text_types(items[0]) == ["input_text"]
    assert _text_types(items[1]) == ["output_text"]
    assert _text_types(items[2]) == ["input_text"]


def test_assistant_history_text_is_output_text_without_thinking_support() -> None:
    _, items = _messages_to_input(
        [
            UserMessage.from_text("question"),
            AssistantMessage(
                contents=[
                    ThinkingContent(content="pondering"),
                    TextContent(content="answer"),
                ]
            ),
        ],
        supports_thinking=False,
    )

    assert items[0]["role"] == "user"
    assert _text_types(items[0]) == ["input_text"]
    # Thinking is merged into the text for non-reasoning models, but the merged
    # part must still be typed for the assistant role.
    assert items[1]["role"] == "assistant"
    assert items[1]["content"] == [{"type": "output_text", "text": "pondering\nanswer"}]


def test_signed_reasoning_replay_precedes_tool_call_and_text_is_output_text() -> None:
    """The reasoning-replay contract must survive the output_text fix: a signed
    ThinkingContent is replayed as a top-level reasoning item immediately before
    the item it justified, with buffered text flushed as an assistant message.
    """
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "blob"}
    _, items = _messages_to_input(
        [
            AssistantMessage(
                contents=[
                    TextContent(content="let me check"),
                    ThinkingContent(content="thought", signature=json.dumps(reasoning_item)),
                    ToolCallContent(id="call_1", name="bash", args={"cmd": "ls"}),
                ]
            ),
        ],
        supports_thinking=True,
    )

    assert items[0] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "let me check"}],
    }
    assert items[1] == reasoning_item
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_1"


# ---------------------------------------------------------------------------
# stream loop — terminal event and stop-reason derivation
# ---------------------------------------------------------------------------


class _FakeRawResponse:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def parse(self) -> Any:
        async def gen() -> Any:
            for event in self._events:
                yield event

        return gen()


class _FakeStreamingCM:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeRawResponse:
        return _FakeRawResponse(self._events)

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _model() -> Model:
    return Model(id="gpt-5.5", name="gpt-5.5", provider="openai", cost=Cost())


def _collect(events: list[Any]) -> list[Any]:
    api = OpenAIResponsesAPI(LLMOptions(api_key="test-key"))
    api._client.responses.with_streaming_response.create = (  # type: ignore[method-assign]
        lambda **kwargs: _FakeStreamingCM(events)
    )
    context = LLMContext(messages=[UserMessage.from_text("hi")])

    async def run() -> list[Any]:
        collected = [e async for e in api.stream(context, _model())]
        await api.aclose()
        return collected

    return asyncio.run(run())


def _completed_event(**response_fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(**response_fields))


def test_stream_emits_end_event_on_response_completed() -> None:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=3, cache_write_tokens=0),
    )
    events = _collect(
        [
            SimpleNamespace(
                type="response.output_item.added", item=SimpleNamespace(type="message")
            ),
            SimpleNamespace(type="response.output_text.delta", delta="hel"),
            SimpleNamespace(type="response.output_text.done", text="hello"),
            _completed_event(usage=usage, status="completed", incomplete_details=None),
        ]
    )

    assert any(isinstance(e, TextEndEvent) and e.text.content == "hello" for e in events)
    end = next(e for e in events if isinstance(e, EndEvent))
    assert end.reason == StopReason.Stop
    assert end.input_tokens == 10
    assert end.output_tokens == 5
    assert end.cache_read_tokens == 3


def test_stream_maps_max_output_tokens_incomplete_to_length() -> None:
    events = _collect(
        [
            SimpleNamespace(
                type="response.incomplete",
                response=SimpleNamespace(
                    usage=None,
                    status="incomplete",
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                ),
            ),
        ]
    )

    end = next(e for e in events if isinstance(e, EndEvent))
    assert end.reason == StopReason.Length


def test_stream_maps_function_calls_to_tool_calls_stop_reason() -> None:
    events = _collect(
        [
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(
                    type="function_call", id="fc_1", call_id="call_1", name="bash"
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.done",
                item_id="fc_1",
                arguments='{"cmd": "ls"}',
            ),
            _completed_event(usage=None, status="completed", incomplete_details=None),
        ]
    )

    tool_end = next(e for e in events if isinstance(e, ToolCallEndEvent))
    assert tool_end.tool_call.id == "call_1"
    assert tool_end.tool_call.args == {"cmd": "ls"}
    end = next(e for e in events if isinstance(e, EndEvent))
    assert end.reason == StopReason.ToolCalls


def test_stream_still_accepts_legacy_response_done() -> None:
    """Responses-API-compatible proxies may still send the legacy terminal
    event name with an explicit stop_reason.
    """
    events = _collect(
        [
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(usage=None, stop_reason="max_output_tokens"),
            ),
        ]
    )

    end = next(e for e in events if isinstance(e, EndEvent))
    assert end.reason == StopReason.Length


class _FakeReasoningItem:
    """Stands in for the SDK's typed reasoning output item."""

    type = "reasoning"
    id = "rs_1"

    def model_dump(self, mode: str = "json", exclude_unset: bool = False) -> dict[str, Any]:
        return {"type": "reasoning", "id": "rs_1", "encrypted_content": "blob"}


def test_stream_reasoning_replay_signature_still_captured() -> None:
    """The reasoning-signature capture (buffered summary text + full item dump
    fired from response.output_item.done) must be unaffected by the terminal
    event fix.
    """
    events = _collect(
        [
            SimpleNamespace(
                type="response.output_item.added", item=SimpleNamespace(type="reasoning")
            ),
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="thou"),
            SimpleNamespace(
                type="response.reasoning_summary_text.done", item_id="rs_1", text="thought"
            ),
            SimpleNamespace(type="response.output_item.done", item=_FakeReasoningItem()),
            SimpleNamespace(
                type="response.output_item.added", item=SimpleNamespace(type="message")
            ),
            SimpleNamespace(type="response.output_text.done", text="answer"),
            _completed_event(usage=None, status="completed", incomplete_details=None),
        ]
    )

    thinking_end = next(e for e in events if isinstance(e, ThinkingEndEvent))
    assert thinking_end.thinking.content == "thought"
    assert json.loads(thinking_end.thinking.signature) == {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "blob",
    }
    end = next(e for e in events if isinstance(e, EndEvent))
    assert end.reason == StopReason.Stop
