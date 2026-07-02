from tau.builtins.models.text import models
from tau.inference.api.text.dialect import (
    CHAT_TEMPLATE,
    OPENROUTER,
    build_reasoning_request_params,
)
from tau.inference.model.types import Model
from tau.inference.types import LLMOptions, ThinkingLevel


def _openrouter_reasoning_model() -> Model:
    return Model(
        id="openai/gpt-oss-120b:free",
        name="GPT-OSS 120B",
        provider="openrouter",
        thinking=True,
        thinking_format=OPENROUTER,
    )


def test_openrouter_reasoning_model_defaults_to_enabled() -> None:
    params = build_reasoning_request_params(
        _openrouter_reasoning_model(),
        LLMOptions(thinking_level=None),
    )

    assert params == {"reasoning": {"enabled": True}}


def test_openrouter_reasoning_model_uses_selected_effort() -> None:
    params = build_reasoning_request_params(
        _openrouter_reasoning_model(),
        LLMOptions(thinking_level=ThinkingLevel.Medium),
    )

    assert params == {"reasoning": {"effort": "medium"}}


def test_diffusiongemma_uses_chat_template_thinking() -> None:
    model = next(model for model in models if model.id == "google/diffusiongemma-26b-a4b-it")

    assert model.thinking_format == CHAT_TEMPLATE
    assert build_reasoning_request_params(
        model,
        LLMOptions(thinking_level=ThinkingLevel.Medium),
    ) == {"chat_template_kwargs": {"enable_thinking": True}}
