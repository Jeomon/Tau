"""Tests for TextLLM.invoke() and TextLLM.stream() retry/empty-response behaviour."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tau.inference.types import (
    EndEvent,
    ErrorEvent,
    LLMContext,
    LLMOptions,
    RetryEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCallEndEvent,
)
from tau.inference.utils import ErrorKind
from tau.message.types import ToolCallContent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_end(content: str) -> TextEndEvent:
    from tau.message.types import TextContent

    return TextEndEvent(text=TextContent(content=content))  # type: ignore[call-arg]


def _text_delta(content: str) -> TextDeltaEvent:
    from tau.message.types import TextContent

    return TextDeltaEvent(text=TextContent(content=content))  # type: ignore[call-arg]


def _tool_call_end() -> ToolCallEndEvent:
    return ToolCallEndEvent(tool_call=ToolCallContent(id="1", name="bash", args={}))


def _error(msg: str, status: int | None = None) -> Exception:
    exc = Exception(msg)
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    return exc


def _make_llm(api_invoke_side_effect=None, max_retries: int = 2):
    """Build a TextLLM with a mocked underlying API."""
    from tau.inference.api.text.service import TextLLM

    options = LLMOptions(
        api_key="test-key",
        max_retries=max_retries,
        retry_base_delay_ms=0,
    )

    llm = object.__new__(TextLLM)

    mock_api = MagicMock()
    mock_api.options = options
    mock_api.invoke = AsyncMock(side_effect=api_invoke_side_effect)
    mock_api.resolve_async = AsyncMock()

    mock_auth = MagicMock()
    mock_auth.get_api_key = AsyncMock(return_value=None)
    mock_auth.is_oauth = MagicMock(return_value=False)

    mock_model = MagicMock()
    mock_model.id = "test-model"

    llm.__dict__["api"] = mock_api
    llm.__dict__["_auth_manager"] = mock_auth
    llm.__dict__["model"] = mock_model
    llm.__dict__["provider_id"] = "test-provider"
    llm.__dict__["_resolve_messages"] = lambda context: context.messages

    return llm


@pytest.mark.asyncio
async def test_invoke_sets_api_key_before_lazy_resolution() -> None:
    llm = _make_llm(api_invoke_side_effect=[])
    llm._auth_manager.get_api_key = AsyncMock(return_value="stored-key")

    async def assert_key_is_set() -> None:
        assert llm.api.options.api_key == "stored-key"

    llm.api.resolve_async = AsyncMock(side_effect=assert_key_is_set)

    await llm.invoke(_context())

    llm.api.resolve_async.assert_awaited_once()


def _context() -> LLMContext:
    from tau.message.types import UserMessage

    return LLMContext(messages=[UserMessage.from_text("hello")])


def _make_stream_llm(responses, max_retries: int = 2):
    """Build a TextLLM with a mocked streaming API."""
    from tau.inference.api.text.service import TextLLM

    options = LLMOptions(
        api_key="test-key",
        max_retries=max_retries,
        retry_base_delay_ms=0,
    )

    call_count = {"n": 0}

    class MockAPI:
        def __init__(self):
            self.options = options

        async def stream(self, ctx, model):
            item = responses[call_count["n"]]
            call_count["n"] += 1
            if isinstance(item, Exception):
                raise item
            for e in item:
                yield e

        async def resolve_async(self):
            pass

    class MockAuth:
        async def get_api_key(self, provider_id):
            return None

        def is_oauth(self, provider_id):
            return False

    class MockModel:
        id = "test-model"

    llm = object.__new__(TextLLM)
    llm.__dict__["api"] = MockAPI()
    llm.__dict__["_auth_manager"] = MockAuth()
    llm.__dict__["model"] = MockModel()
    llm.__dict__["provider_id"] = "test-provider"
    llm.__dict__["_resolve_messages"] = lambda context: context.messages

    return llm, call_count


async def _collect_stream(llm, context):
    events = []
    async for event in llm.stream(context):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# invoke() — transient error retries
# ---------------------------------------------------------------------------


class TestInvokeRetryOnTransientError:
    def test_retries_on_rate_limit_then_succeeds(self):
        async def _run():
            llm = _make_llm([_error("rate limit exceeded", 429), [_text_end("summary")]])
            result = await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2
            assert any(isinstance(e, TextEndEvent) for e in result)

        asyncio.run(_run())

    def test_retries_on_server_error_then_succeeds(self):
        async def _run():
            llm = _make_llm([_error("internal server error", 500), [_text_end("ok")]])
            result = await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2
            assert any(isinstance(e, TextEndEvent) for e in result)

        asyncio.run(_run())

    def test_returns_error_event_after_exhausting_retries(self):
        async def _run():
            llm = _make_llm(
                [_error("rate limit", 429)] * 3,
                max_retries=2,
            )
            result = await llm.invoke(_context())
            assert any(isinstance(e, ErrorEvent) for e in result)
            assert llm.api.invoke.call_count == 3

        asyncio.run(_run())

    def test_no_retry_on_non_retryable_error(self):
        async def _run():
            llm = _make_llm([_error("invalid api key", 401)])
            result = await llm.invoke(_context())
            assert any(isinstance(e, ErrorEvent) for e in result)
            assert llm.api.invoke.call_count == 1

        asyncio.run(_run())

    def test_error_event_carries_kind(self):
        async def _run():
            llm = _make_llm([_error("rate limit", 429)] * 3, max_retries=2)
            result = await llm.invoke(_context())
            error_event = next(e for e in result if isinstance(e, ErrorEvent))
            assert error_event.kind == ErrorKind.RATE_LIMIT

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# invoke() — empty response retries
# ---------------------------------------------------------------------------


class TestInvokeRetryOnEmptyResponse:
    def test_retries_on_blank_text_end_event(self):
        async def _run():
            llm = _make_llm([[_text_end("")], [_text_end("real summary")]])
            result = await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2
            text_end = next(e for e in result if isinstance(e, TextEndEvent))
            assert text_end.text.content == "real summary"

        asyncio.run(_run())

    def test_retries_on_whitespace_only_text(self):
        async def _run():
            llm = _make_llm([[_text_end("   \n  ")], [_text_end("content")]])
            await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2

        asyncio.run(_run())

    def test_retries_on_no_text_events_at_all(self):
        async def _run():
            llm = _make_llm([[EndEvent()], [_text_end("ok")]])
            await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2

        asyncio.run(_run())

    def test_does_not_retry_when_tool_calls_present(self):
        async def _run():
            llm = _make_llm([[_tool_call_end()]])
            result = await llm.invoke(_context())
            assert llm.api.invoke.call_count == 1
            assert any(isinstance(e, ToolCallEndEvent) for e in result)

        asyncio.run(_run())

    def test_returns_empty_after_exhausting_retries(self):
        async def _run():
            llm = _make_llm([[_text_end("")]] * 3, max_retries=2)
            result = await llm.invoke(_context())
            assert llm.api.invoke.call_count == 3
            assert any(isinstance(e, TextEndEvent) for e in result)

        asyncio.run(_run())

    def test_delta_events_blank_also_retries(self):
        async def _run():
            llm = _make_llm(
                [[_text_delta(""), _text_end("")]],
            )
            # exhaust retries with blank deltas, last attempt returns good content
            llm.api.invoke.side_effect = [
                [_text_delta(""), _text_end("")],
                [_text_delta("hello"), _text_end("hello")],
            ]
            await llm.invoke(_context())
            assert llm.api.invoke.call_count == 2

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# stream() — empty response retries
# ---------------------------------------------------------------------------


class TestStreamRetryOnEmptyResponse:
    def test_retries_when_no_content_events(self):
        async def _run():
            llm, call_count = _make_stream_llm(
                [
                    [StartEvent(), EndEvent()],
                    [StartEvent(), _text_end("hello")],
                ]
            )
            events = await _collect_stream(llm, _context())
            assert call_count["n"] == 2
            assert any(isinstance(e, TextEndEvent) for e in events)

        asyncio.run(_run())

    def test_emits_retry_event_before_retry(self):
        async def _run():
            llm, _ = _make_stream_llm(
                [
                    [StartEvent(), EndEvent()],
                    [StartEvent(), _text_end("ok")],
                ]
            )
            events = await _collect_stream(llm, _context())
            assert any(isinstance(e, RetryEvent) for e in events)

        asyncio.run(_run())

    def test_does_not_retry_when_text_present(self):
        async def _run():
            llm, call_count = _make_stream_llm(
                [
                    [StartEvent(), _text_end("content"), EndEvent()],
                ]
            )
            await _collect_stream(llm, _context())
            assert call_count["n"] == 1

        asyncio.run(_run())

    def test_does_not_retry_when_tool_calls_present(self):
        async def _run():
            llm, call_count = _make_stream_llm(
                [
                    [StartEvent(), _tool_call_end(), EndEvent()],
                ]
            )
            await _collect_stream(llm, _context())
            assert call_count["n"] == 1

        asyncio.run(_run())

    def test_returns_after_exhausting_retries(self):
        async def _run():
            empty = [StartEvent(), EndEvent()]
            llm, call_count = _make_stream_llm([empty, empty, empty], max_retries=2)
            await _collect_stream(llm, _context())
            assert call_count["n"] == 3

        asyncio.run(_run())

    def test_retries_on_exception_before_content(self):
        async def _run():
            llm, call_count = _make_stream_llm(
                [
                    _error("rate limit", 429),
                    [StartEvent(), _text_end("ok")],
                ]
            )
            events = await _collect_stream(llm, _context())
            assert call_count["n"] == 2
            assert any(isinstance(e, TextEndEvent) for e in events)

        asyncio.run(_run())
