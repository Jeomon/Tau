from __future__ import annotations

import logging
from dataclasses import fields

from tau.auth.manager import AuthManager
from tau.inference.api.audio.registry import AudioAPIRegistry
from tau.inference.api.registry import LazyAPI
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.registry import AudioProviderRegistry, ProviderRegistry
from tau.inference.types import (
    AudioOptions,
    STTContext,
    SynthesizedAudio,
    TranscribedAudio,
    TTSContext,
)

_log = logging.getLogger(__name__)


class AudioLLM:
    """Service for audio synthesis and transcription using audio APIs."""

    # Class-level registries — None until first use (lazy) or explicitly set by
    # RuntimeContext.create(), whichever comes first. This avoids importing all
    # audio provider SDKs (gemini, elevenlabs, …) at module import time.
    _models: ModelRegistry | None = None
    _providers: AudioProviderRegistry | None = None
    _apis: AudioAPIRegistry | None = None
    _auth_manager: AuthManager | None = None

    @classmethod
    def _ensure_defaults(cls) -> None:
        """Lazily initialize default registries."""
        if cls._models is None:
            cls._models = ModelRegistry.from_audio_builtins()
            cls._providers = AudioProviderRegistry.from_builtins()
            cls._apis = AudioAPIRegistry.from_builtins()
            cls._auth_manager = AuthManager.create(
                ProviderRegistry(audio=AudioProviderRegistry.from_builtins())
            )

    def __init__(
        self,
        model_id: str,
        provider: str | None = None,
        options: AudioOptions | None = None,
        *,
        models: ModelRegistry | None = None,
        providers: AudioProviderRegistry | None = None,
        apis: AudioAPIRegistry | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        type(self)._ensure_defaults()
        # Capture to locals — Pyright narrows local variables after assert,
        # but does not narrow class attribute access.
        _models = models or type(self)._models
        _providers = providers or type(self)._providers
        _apis = apis or type(self)._apis
        _auth_manager = auth_manager or type(self)._auth_manager
        assert (
            _models is not None
            and _providers is not None
            and _apis is not None
            and _auth_manager is not None
        )

        model = _models.get(model_id, provider)
        if model is None:
            raise ValueError(f"Audio model '{model_id}' not found.")

        prov = _providers.get(model.provider)
        if prov is None:
            raise ValueError(f"Audio provider '{model.provider}' not found.")

        api_name = model.api or prov.api

        self.model = model
        self.provider_id = prov.name
        self._auth_manager = _auth_manager

        base_url = model.base_url or prov.base_url
        base_opts = AudioOptions(base_url=base_url)
        # Lazy adapter: defers importing the provider SDK and building its client
        # until the first synthesize()/transcribe() call.
        self.api = LazyAPI(_apis, api_name, self._merge_options(base_opts, options))

    def _merge_options(self, base: AudioOptions, override: AudioOptions | None) -> AudioOptions:
        """Merge base options with override options."""
        if override is None:
            return base
        merged = AudioOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def synthesize(self, context: TTSContext) -> SynthesizedAudio:
        """Synthesize audio from text."""
        assert self._auth_manager is not None
        api_key = await self._auth_manager.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        if self.model.tts_format:
            from tau.inference.types import AudioFormat

            fmt = AudioFormat(self.model.tts_format)
            from dataclasses import replace

            context = replace(context, response_format=fmt)
        try:
            return await self.api.synthesize(self.model, context)
        except Exception as e:
            _log.error(
                "synthesize failed: provider=%s model=%s: %s",
                self.provider_id,
                self.model.name,
                e,
                exc_info=True,
            )
            raise

    async def transcribe(self, context: STTContext) -> TranscribedAudio:
        """Transcribe audio to text."""
        assert self._auth_manager is not None
        api_key = await self._auth_manager.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        try:
            return await self.api.transcribe(self.model, context)
        except Exception as e:
            _log.error(
                "transcribe failed: provider=%s model=%s: %s",
                self.provider_id,
                self.model.name,
                e,
                exc_info=True,
            )
            raise
