from tau.inference.api.text.dialect import OPENROUTER, build_reasoning_request_params
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
