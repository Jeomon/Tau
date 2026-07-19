"""Model-resolution tests for TextLLM, covering the custom-model-id fallback.

A pinned provider with no exact-match model shouldn't hard-fail: if that
provider already has at least one other registered model, TextLLM synthesizes
a fallback Model (borrowing defaults from an existing model on that provider)
under the requested id, matching pi's buildFallbackModel behavior. This lets a
custom/unregistered model id on a known provider actually run instead of
being rejected outright.
"""

from __future__ import annotations

import pytest

from tau.inference.api.text.service import TextLLM
from tau.inference.model.registry import ModelRegistry
from tau.inference.model.types import Model
from tau.inference.provider.registry import TextProviderRegistry
from tau.inference.provider.types import APIProvider
from tau.inference.types import LLMOptions, ThinkingLevel


def _groq_registries() -> tuple[ModelRegistry, TextProviderRegistry]:
    """A minimal models/providers pair standing in for the deleted builtin catalog."""
    models = ModelRegistry()
    models.register(
        Model(
            id="llama-3.3-70b-versatile",
            name="Llama 3.3 70B Versatile",
            provider="groq",
            context_window=131_072,
        )
    )
    providers = TextProviderRegistry()
    providers.register(
        APIProvider(id="groq", name="Groq", api="openai_completions", options=LLMOptions())
    )
    return models, providers


def test_custom_model_id_on_known_provider_falls_back() -> None:
    models, providers = _groq_registries()
    llm = TextLLM(
        model_id="some-totally-custom-model-xyz",
        provider="groq",
        options=LLMOptions(api_key="fake-key-for-test"),
        models=models,
        providers=providers,
    )

    assert llm.model.id == "some-totally-custom-model-xyz"
    assert llm.model.name == "some-totally-custom-model-xyz"
    assert llm.model.provider == "groq"
    assert llm.provider_id == "groq"
    assert llm.fallback_reason == (
        "Model 'some-totally-custom-model-xyz' not found for provider 'groq'. "
        "Using custom model id."
    )


def test_custom_model_id_inherits_provider_defaults() -> None:
    models, providers = _groq_registries()
    llm = TextLLM(
        model_id="another-custom-id",
        provider="groq",
        options=LLMOptions(api_key="fake-key-for-test"),
        models=models,
        providers=providers,
    )
    # Cost/context/api defaults are borrowed from an existing model on the
    # same provider rather than left at empty dataclass defaults.
    assert llm.model.context_window > 0


def test_known_model_on_known_provider_resolves_normally_without_fallback() -> None:
    models, providers = _groq_registries()
    llm = TextLLM(
        model_id="llama-3.3-70b-versatile",
        provider="groq",
        options=LLMOptions(api_key="fake-key-for-test"),
        models=models,
        providers=providers,
    )
    assert llm.model.id == "llama-3.3-70b-versatile"
    assert llm.fallback_reason is None


def test_unknown_provider_still_raises() -> None:
    models, providers = _groq_registries()
    with pytest.raises(ValueError, match="not found"):
        TextLLM(
            model_id="whatever",
            provider="not-a-real-provider-xyz",
            options=LLMOptions(),
            models=models,
            providers=providers,
        )


def test_unknown_model_without_pinned_provider_still_raises() -> None:
    # Fallback only applies when a provider is explicitly pinned — without one
    # there's no provider whose defaults to borrow, so this must still fail.
    models, providers = _groq_registries()
    with pytest.raises(ValueError, match="not found"):
        TextLLM(
            model_id="totally-made-up-model-123",
            options=LLMOptions(),
            models=models,
            providers=providers,
        )


class TestCustomModelIdThinkingSuffix:
    """A trailing ':<level>' on a fallback model id sets its default thinking level."""

    def test_valid_suffix_is_stripped_and_sets_thinking_level(self) -> None:
        models, providers = _groq_registries()
        llm = TextLLM(
            model_id="my-custom-model:high",
            provider="groq",
            options=LLMOptions(api_key="fake-key-for-test"),
            models=models,
            providers=providers,
        )
        assert llm.model.id == "my-custom-model"
        assert llm.model.name == "my-custom-model"
        assert llm.model.thinking is True
        assert llm.model.thinking_levels == [ThinkingLevel.High]
        assert llm.model.default_thinking_level == ThinkingLevel.High

    def test_invalid_suffix_is_kept_as_part_of_the_id(self) -> None:
        models, providers = _groq_registries()
        llm = TextLLM(
            model_id="my-custom-model:notarealvalue",
            provider="groq",
            options=LLMOptions(api_key="fake-key-for-test"),
            models=models,
            providers=providers,
        )
        assert llm.model.id == "my-custom-model:notarealvalue"
        assert llm.model.thinking_levels == []
        assert llm.model.default_thinking_level is None

    def test_explicit_thinking_level_option_wins_suffix_is_not_stripped(self) -> None:
        models, providers = _groq_registries()
        llm = TextLLM(
            model_id="my-custom-model:high",
            provider="groq",
            options=LLMOptions(api_key="fake-key-for-test", thinking_level=ThinkingLevel.Low),
            models=models,
            providers=providers,
        )
        assert llm.model.id == "my-custom-model:high"
