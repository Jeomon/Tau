"""A populated ``content_block_start`` must not be discarded.

Anthropic's own API practically always opens a block empty and sends every
character as a delta, so seeding the accumulator with ``""`` looks harmless.
Other servers speaking the same wire format — LiteLLM and similar gateways,
self-hosted proxies — do put content in the start event, and that content was
silently dropped: the first chunk of every text and thinking block vanished
while the deltas that followed arrived intact.

The same handler already seeds ``redacted_thinking`` with real content, which
is what makes the empty-seeding an oversight rather than a decision.
"""

from __future__ import annotations

import types

import pytest

from tau.inference.api.text.anthropic_messages import AnthropicMessagesAPI
from tau.inference.model.types import Model
from tau.inference.types import LLMOptions

_MODEL = Model(id="claude-sonnet-5", name="Claude Sonnet 5", provider="anthropic")


def _ev(**kw):
    return types.SimpleNamespace(**kw)


#: A gateway-shaped stream: content present in the *start* event, remainder in
#: deltas. Byte-identical in meaning to an all-deltas stream.
_EVENTS = [
    _ev(
        type="message_start",
        message=_ev(
            usage=_ev(
                input_tokens=1,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )
        ),
    ),
    _ev(
        type="content_block_start",
        index=0,
        content_block=_ev(type="thinking", thinking="SEEDED-THINK "),
    ),
    _ev(
        type="content_block_delta",
        index=0,
        delta=_ev(type="thinking_delta", thinking="tail-think"),
    ),
    _ev(type="content_block_stop", index=0),
    _ev(
        type="content_block_start",
        index=1,
        content_block=_ev(type="text", text="SEEDED-TEXT "),
    ),
    _ev(
        type="content_block_delta",
        index=1,
        delta=_ev(type="text_delta", text="tail-text"),
    ),
    _ev(type="content_block_stop", index=1),
    _ev(
        type="message_delta",
        delta=_ev(stop_reason="end_turn"),
        usage=_ev(output_tokens=5),
    ),
]


class _FakeStream:
    def __aiter__(self):
        async def gen():
            for event in _EVENTS:
                yield event

        return gen()


class _FakeRaw:
    http_response = _ev(status_code=200, headers={})

    async def parse(self):
        return _FakeStream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeMessages:
    class _WithStreamingResponse:
        def create(self, **_kw):
            return _FakeRaw()

    with_streaming_response = _WithStreamingResponse()


async def _collect(api) -> dict[str, str]:
    """Run the real stream() and return the final text/thinking payloads."""
    context = _ev(
        messages=[],
        system_prompt="",
        tools=None,
        ephemeral_message_count=0,
        response_format=None,
    )
    ends: dict[str, str] = {}
    async for event in api.stream(context, _MODEL):
        name = type(event).__name__
        if name == "TextEndEvent":
            ends["text"] = event.text.content
        elif name == "ThinkingEndEvent":
            thinking = getattr(event, "thinking", None)
            ends["thinking"] = getattr(thinking, "content", "") or ""
    return ends


@pytest.fixture
def api(monkeypatch):
    llm = AnthropicMessagesAPI(LLMOptions())
    monkeypatch.setattr(llm, "_client", _ev(messages=_FakeMessages()), raising=False)
    monkeypatch.setattr(llm, "_cancelled", lambda: False, raising=False)
    monkeypatch.setattr(llm, "_build_params", lambda *a, **k: {}, raising=False)
    return llm


@pytest.mark.asyncio
async def test_text_in_start_block_is_not_dropped(api):
    ends = await _collect(api)
    assert ends.get("text") == "SEEDED-TEXT tail-text"


@pytest.mark.asyncio
async def test_thinking_in_start_block_is_not_dropped(api):
    ends = await _collect(api)
    assert ends.get("thinking") == "SEEDED-THINK tail-think"
