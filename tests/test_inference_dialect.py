from tau.inference.api.text.dialect import build_reasoning_request_params
from tau.inference.model.types import Model
from tau.inference.types import LLMOptions, ThinkingLevel


def _reasoning_model() -> Model:
    return Model(
        id="openai/gpt-oss-120b:free",
        name="GPT-OSS 120B",
        provider="openrouter",
        thinking=True,
    )


def test_reasoning_model_no_level_returns_no_params() -> None:
    params = build_reasoning_request_params(
        _reasoning_model(),
        LLMOptions(thinking_level=None),
    )

    assert params == {}


def test_reasoning_model_uses_selected_effort() -> None:
    params = build_reasoning_request_params(
        _reasoning_model(),
        LLMOptions(thinking_level=ThinkingLevel.Medium),
    )

    assert params == {"reasoning_effort": "medium"}


def test_non_thinking_model_returns_no_params() -> None:
    model = Model(id="gpt-4o", name="GPT-4o", provider="openai", thinking=False)
    params = build_reasoning_request_params(model, LLMOptions(thinking_level=ThinkingLevel.High))

    assert params == {}
