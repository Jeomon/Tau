from __future__ import annotations

import base64
import time
from typing import Any

from tau.inference.api.audio.base import RestAudioAPI
from tau.inference.model.types import Model
from tau.inference.types import (
    AudioFormat,
    AudioStopReason,
    STTContext,
    SynthesizedAudio,
    TimestampGranularity,
    TranscribedAudio,
    TTSContext,
    WordTimestamp,
)

_BASE_URL = "https://api.sarvam.ai"
_TTS_ENDPOINT = "/text-to-speech"
_STT_ENDPOINT = "/speech-to-text"

# Sarvam returns WAV by default inside the base64 payload
_OUTPUT_FORMAT = AudioFormat.WAV


class SarvamAudioAPI(RestAudioAPI):
    """
    Sarvam AI audio API — proprietary REST endpoints for Indian language TTS and STT.
    Auth via custom header: api-subscription-key.
    TTS response: JSON with base64-encoded audio in audios[].
    STT response: JSON with transcript, language_code, language_probability.
    """

    base_url = _BASE_URL
    api_key_header = "api-subscription-key"

    async def synthesize(self, model: Model, context: TTSContext) -> SynthesizedAudio:
        payload: dict[str, Any] = {
            "model": model.id,
            "text": context.input,
            "speaker": context.voice,
            "pace": context.speed,
        }
        if context.language:
            payload["target_language_code"] = context.language

        if self.options.on_payload:
            modified = self.options.on_payload(payload)
            if modified is not None:
                payload = modified

        try:
            async with self._new_client() as http:
                response = await http.post(
                    _TTS_ENDPOINT,
                    json=payload,
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
                data = response.json()

            if self.options.on_response:
                self.options.on_response(data)

            audio_bytes = base64.b64decode(data["audios"][0])

            return SynthesizedAudio(
                model_id=model.id,
                provider=model.provider,
                audio=audio_bytes,
                format=_OUTPUT_FORMAT,
                stop_reason=AudioStopReason.Stop,
                timestamp=time.time(),
            )
        except Exception as exc:
            return self.synthesis_error(model, exc, _OUTPUT_FORMAT)

    async def transcribe(self, model: Model, context: STTContext) -> TranscribedAudio:
        filename = f"audio.{context.format.value}"
        files = {"file": (filename, context.audio, f"audio/{context.format.value}")}
        data: dict[str, Any] = {"model": model.id}

        if context.language:
            data["language_code"] = context.language
        if context.prompt:
            # prompt field carries Sarvam's mode: transcribe/translate/verbatim/translit/codemix
            data["mode"] = context.prompt
        if TimestampGranularity.Word in context.timestamp_granularities:
            data["with_timestamps"] = "true"

        if self.options.on_payload:
            modified = self.options.on_payload({**data, "_files": files})
            if modified is not None:
                files = modified.pop("_files", files)
                data = modified

        try:
            async with self._new_client() as http:
                response = await http.post(
                    _STT_ENDPOINT,
                    data=data,
                    files=files,
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
                result = response.json()

            if self.options.on_response:
                self.options.on_response(result)

            words: list[WordTimestamp] = []
            if ts := result.get("timestamps"):
                timestamp_words = ts.get("words", [])
                starts = ts.get("start_time_seconds", [])
                ends = ts.get("end_time_seconds", [])
                words = [
                    WordTimestamp(word=word, start=start, end=end)
                    for word, start, end in zip(timestamp_words, starts, ends, strict=False)
                ]

            return TranscribedAudio(
                model_id=model.id,
                provider=model.provider,
                text=result.get("transcript", ""),
                language=result.get("language_code"),
                words=words,
                stop_reason=AudioStopReason.Stop,
                usage={"language_probability": result.get("language_probability")},
                timestamp=time.time(),
            )
        except Exception as exc:
            return self.transcription_error(model, exc)
