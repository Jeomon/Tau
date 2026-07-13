"""Tests for the built-in Subconscious provider and model catalogue."""

from tau.builtins.models.text import models
from tau.builtins.providers.text import api_providers
from tau.inference.model.types import Modality


def test_subconscious_provider_uses_openai_completions() -> None:
    provider = next(provider for provider in api_providers if provider.id == "subconscious")

    assert provider.api == "openai_completions"
    assert provider.options.base_url == "https://api.subconscious.dev/v1"


def test_subconscious_models_match_verified_catalogue() -> None:
    subconscious_models = {model.id: model for model in models if model.provider == "subconscious"}

    assert set(subconscious_models) == {"subconscious/tim-qwen3.6-27b"}

    tim = subconscious_models["subconscious/tim-qwen3.6-27b"]
    assert tim.cost.input == 0.30
    assert tim.cost.output == 3.00
    assert tim.cost.cache_read == 0.15
    assert tim.thinking is True
    assert tim.thinking_format == "chat-template"
    assert tim.context_window == 0
    assert tim.input == [Modality.Text, Modality.Image]
