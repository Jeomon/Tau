from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from tau.inference.model.types import Model
from tau.inference.types import (
    AudioFormat,
    AudioOptions,
    AudioStopReason,
    STTContext,
    SynthesizedAudio,
    TranscribedAudio,
    TTSContext,
)


class BaseAudioAPI(ABC):
    """Abstract base class for audio API implementations."""

    def __init__(self, options: AudioOptions) -> None:
        self.options = options

    @abstractmethod
    async def synthesize(self, model: Model, context: TTSContext) -> SynthesizedAudio:
        """Convert text to speech."""
        raise NotImplementedError

    @abstractmethod
    async def transcribe(self, model: Model, context: STTContext) -> TranscribedAudio:
        """Convert speech to text."""
        raise NotImplementedError

    # ── Failure results ───────────────────────────────────────────────────────
    #
    # Both calls report failure as data rather than raising: an audio error is a
    # normal outcome for the caller (no key, model offline, bad input) and the
    # result already carries a stop_reason for it.

    @staticmethod
    def synthesis_error(model: Model, exc: Exception, fmt: AudioFormat) -> SynthesizedAudio:
        """Empty synthesis result carrying ``exc``. ``fmt`` is the provider's own."""
        return SynthesizedAudio(
            model_id=model.id,
            provider=model.provider,
            audio=b"",
            format=fmt,
            stop_reason=AudioStopReason.Error,
            error=str(exc),
            timestamp=time.time(),
        )

    @staticmethod
    def transcription_error(model: Model, exc: Exception) -> TranscribedAudio:
        """Empty transcription result carrying ``exc``."""
        return TranscribedAudio(
            model_id=model.id,
            provider=model.provider,
            text="",
            stop_reason=AudioStopReason.Error,
            error=str(exc),
            timestamp=time.time(),
        )


class RestAudioAPI(BaseAudioAPI):
    """Base for audio providers reached over plain REST rather than a vendor SDK.

    Subclasses set ``base_url`` and ``api_key_header``; everything else about
    building the client and the auth headers is identical between them.
    """

    #: Provider default, used when ``options.base_url`` is unset.
    base_url: str = ""
    #: Header the provider expects the API key in.
    api_key_header: str = ""

    def _new_client(self) -> httpx.AsyncClient:
        # Per-call client (used inside `async with`) so its connection pool is
        # always closed — no persistent client left unclosed for the GC.
        from tau.utils.ssl_context import get_shared_ssl_context

        return httpx.AsyncClient(
            base_url=self.options.base_url or self.base_url,
            timeout=self.options.timeout.total_seconds(),
            verify=get_shared_ssl_context(),
        )

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.options.api_key:
            headers[self.api_key_header] = self.options.api_key
        if self.options.headers:
            headers.update(self.options.headers)
        return headers
