"""Regression tests for provider-specific speech transcription payloads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from tau.inference.api.audio.openai_audio import OpenAIAudioAPI
from tau.inference.api.audio.sarvam_audio import SarvamAudioAPI
from tau.inference.model.types import Cost, Model
from tau.inference.types import (
    AudioOptions,
    STTContext,
    TimestampGranularity,
)


def _model(model_id: str, provider: str) -> Model:
    return Model(id=model_id, name=model_id, provider=provider, cost=Cost())


def test_gpt_4o_transcribe_uses_plain_json_without_timestamps() -> None:
    payloads: list[dict[str, Any]] = []
    api = OpenAIAudioAPI(AudioOptions(api_key="test", on_payload=payloads.append))
    create = AsyncMock(return_value=SimpleNamespace(text="hello"))
    api._client = MagicMock()
    api._client.audio.transcriptions.create = create

    result = asyncio.run(
        api.transcribe(
            _model("gpt-4o-transcribe", "openai"),
            STTContext(
                audio=b"audio",
                timestamp_granularities=[TimestampGranularity.Word],
            ),
        )
    )

    assert result.text == "hello"
    assert payloads[0]["response_format"] == "json"
    assert "timestamp_granularities" not in payloads[0]


def test_whisper_requests_and_extracts_detailed_timestamps() -> None:
    payloads: list[dict[str, Any]] = []
    response = SimpleNamespace(
        text="hello",
        language="en",
        duration=0.5,
        words=[SimpleNamespace(word="hello", start=0.0, end=0.5)],
        segments=[SimpleNamespace(id=0, text="hello", start=0.0, end=0.5)],
    )
    api = OpenAIAudioAPI(AudioOptions(api_key="test", on_payload=payloads.append))
    api._client = MagicMock()
    api._client.audio.transcriptions.create = AsyncMock(return_value=response)

    result = asyncio.run(
        api.transcribe(
            _model("whisper-large-v3", "groq"),
            STTContext(
                audio=b"audio",
                timestamp_granularities=[
                    TimestampGranularity.Word,
                    TimestampGranularity.Segment,
                ],
            ),
        )
    )

    assert payloads[0]["response_format"] == "verbose_json"
    assert payloads[0]["timestamp_granularities"] == ["word", "segment"]
    assert result.words[0].word == "hello"
    assert result.segments[0].text == "hello"


def test_sarvam_requests_and_extracts_word_timestamps() -> None:
    payloads: list[dict[str, Any]] = []
    response_data = {
        "transcript": "namaste duniya",
        "language_code": "hi-IN",
        "timestamps": {
            "words": ["namaste", "duniya"],
            "start_time_seconds": [0.0, 0.5],
            "end_time_seconds": [0.4, 1.0],
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert b'name="with_timestamps"' in body
        assert b"true" in body
        return httpx.Response(200, json=response_data)

    api = SarvamAudioAPI(AudioOptions(api_key="test", on_payload=payloads.append))
    transport = httpx.MockTransport(handler)
    api._new_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url="https://api.sarvam.ai",
        transport=transport,
    )

    result = asyncio.run(
        api.transcribe(
            _model("saaras:v3", "sarvam"),
            STTContext(
                audio=b"audio",
                timestamp_granularities=[TimestampGranularity.Word],
            ),
        )
    )

    assert payloads[0]["with_timestamps"] == "true"
    assert [(word.word, word.start, word.end) for word in result.words] == [
        ("namaste", 0.0, 0.4),
        ("duniya", 0.5, 1.0),
    ]
