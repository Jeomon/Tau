from __future__ import annotations

import logging
from dataclasses import fields

from tau.auth.manager import AuthManager
from tau.inference.api.registry import LazyAPI
from tau.inference.api.video.registry import VideoAPIRegistry
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.registry import ProviderRegistry, VideoProviderRegistry
from tau.inference.types import GeneratedVideo, VideoContext, VideoOptions

_log = logging.getLogger(__name__)


class VideoLLM:
    """Service for video generation using video generation APIs."""

    _models = ModelRegistry()
    _providers = VideoProviderRegistry()
    _apis = VideoAPIRegistry.from_builtins()
    _auth_manager = AuthManager.create(ProviderRegistry(video=VideoProviderRegistry()))

    @classmethod
    def list_available(cls) -> list:
        """Return all video models whose provider has usable auth (credential or env var)."""
        from tau.inference.api.availability import available_models

        return available_models(cls._models, cls._providers, cls._auth_manager)

    def __init__(
        self,
        model_id: str,
        provider: str | None = None,
        options: VideoOptions | None = None,
        *,
        models: ModelRegistry | None = None,
        providers: VideoProviderRegistry | None = None,
        apis: VideoAPIRegistry | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        _models = models if models is not None else type(self)._models
        _providers = providers if providers is not None else type(self)._providers
        _apis = apis if apis is not None else type(self)._apis
        self._auth_manager = auth_manager if auth_manager is not None else type(self)._auth_manager

        model = _models.get(model_id, provider)
        if model is None:
            raise ValueError(f"Video model '{model_id}' not found.")

        prov = _providers.get(model.provider)
        if prov is None:
            raise ValueError(f"Video provider '{model.provider}' not found.")

        api_name = model.api or prov.api

        self.model = model
        self.provider_id = prov.id

        base_url = model.base_url or prov.base_url
        base_opts = VideoOptions(base_url=base_url)
        # Lazy adapter: defers importing the provider SDK and building its client
        # until the first generate() call.
        self.api = LazyAPI(_apis, api_name, self._merge_options(base_opts, options))

    def _merge_options(self, base: VideoOptions, override: VideoOptions | None) -> VideoOptions:
        """Merge base options with override options."""
        if override is None:
            return base
        merged = VideoOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def generate(self, context: VideoContext) -> GeneratedVideo:
        """Generate a video from the given context."""
        api_key = await self._auth_manager.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        try:
            return await self.api.generate(self.model, context)
        except Exception as e:
            _log.error(
                "generate failed: provider=%s model=%s: %s",
                self.provider_id,
                self.model.name,
                e,
                exc_info=True,
            )
            raise
