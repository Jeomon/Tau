from __future__ import annotations

from tau.inference.model.types import Model


class ModelRegistry:
    """Lookup table for Model descriptors, supporting multiple providers per model ID."""

    def __init__(self) -> None:
        # A single model id may be served by several providers; store all variants
        self._models: dict[str, list[Model]] = {}

    def register(self, model: Model) -> None:
        """Append a model variant; multiple providers for the same id are allowed."""
        self._models.setdefault(model.id, []).append(model)

    def unregister(self, model_id: str, provider: str | None = None) -> None:
        """Remove a model by id; if provider is given, only remove that provider's variant."""
        if provider is None:
            self._models.pop(model_id, None)
        else:
            remaining = [m for m in self._models.get(model_id, []) if m.provider != provider]
            if remaining:
                self._models[model_id] = remaining
            else:
                self._models.pop(model_id, None)

    def list(self) -> list[Model]:
        """Return all registered model variants across all providers."""
        return [m for models in self._models.values() for m in models]

    def get(self, model_id: str, provider: str | None = None) -> Model | None:
        """Return a model by id, optionally filtered to a specific provider; first match wins."""
        models = self._models.get(model_id, [])
        if not models:
            return None
        if provider is None:
            return models[0]
        return next((m for m in models if m.provider == provider), None)

    def unregister_by_provider(self, provider: str) -> None:
        """Remove all model variants that belong to the given provider."""
        to_delete = []
        for model_id, variants in self._models.items():
            remaining = [m for m in variants if m.provider != provider]
            if remaining:
                self._models[model_id] = remaining
            else:
                to_delete.append(model_id)
        for model_id in to_delete:
            del self._models[model_id]

    def reset(self) -> None:
        """Remove all registered models."""
        self._models.clear()
