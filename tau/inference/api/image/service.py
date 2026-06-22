from __future__ import annotations

import logging
from dataclasses import fields

from tau.auth.manager import AuthManager
from tau.inference.api.image.registry import ImageAPIRegistry
from tau.inference.api.registry import LazyAPI
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.registry import ImageProviderRegistry, ProviderRegistry
from tau.inference.types import GeneratedImage, ImageContext, ImageOptions

_log = logging.getLogger(__name__)


class ImageLLM:
    """Service for image generation using image generation APIs."""

    _models = ModelRegistry.from_image_builtins()
    _providers = ImageProviderRegistry.from_builtins()
    _apis = ImageAPIRegistry.from_builtins()
    _auth_manager = AuthManager.create(
        ProviderRegistry(image=ImageProviderRegistry.from_builtins())
    )

    def __init__(
        self,
        model_id: str,
        options: ImageOptions | None = None,
        *,
        models: ModelRegistry | None = None,
        providers: ImageProviderRegistry | None = None,
        apis: ImageAPIRegistry | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        _models = models if models is not None else type(self)._models
        _providers = providers if providers is not None else type(self)._providers
        _apis = apis if apis is not None else type(self)._apis
        self._auth_manager = auth_manager if auth_manager is not None else type(self)._auth_manager

        model = _models.get(model_id)
        if model is None:
            raise ValueError(f"Image model '{model_id}' not found.")

        provider = _providers.get(model.provider)
        if provider is None:
            raise ValueError(f"Image provider '{model.provider}' not found.")

        api_name = model.api or provider.api

        self.model = model
        self.provider_id = provider.name

        base_url = model.base_url or provider.base_url
        base_opts = ImageOptions(base_url=base_url)
        # Lazy adapter: defers importing the provider SDK and building its client
        # until the first generate() call.
        self.api = LazyAPI(_apis, api_name, self._merge_options(base_opts, options))

    def _merge_options(self, base: ImageOptions, override: ImageOptions | None) -> ImageOptions:
        """Merge base options with override options."""
        if override is None:
            return base
        merged = ImageOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def generate(self, context: ImageContext) -> GeneratedImage:
        """Generate an image from a text prompt."""
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
