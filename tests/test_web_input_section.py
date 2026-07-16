from __future__ import annotations

from types import SimpleNamespace

from tau.inference.model.types import Model
from tau.inference.types import ThinkingLevel
from tau.modes.web.components.input_section import InputSection


def _section_for_model(model: Model) -> InputSection:
    runtime = SimpleNamespace(
        agent=SimpleNamespace(_engine=SimpleNamespace(llm=SimpleNamespace(model=model)))
    )
    return InputSection(runtime)  # type: ignore[arg-type]


def test_effort_levels_follow_model_supported_levels() -> None:
    model = Model(
        id="reasoning-model",
        name="Reasoning Model",
        provider="test",
        thinking=True,
        thinking_levels=[ThinkingLevel.Low, ThinkingLevel.Medium, ThinkingLevel.High],
    )

    assert _section_for_model(model)._available_effort_levels() == [
        ThinkingLevel.Low,
        ThinkingLevel.Medium,
        ThinkingLevel.High,
    ]


def test_effort_levels_fall_back_to_all_levels_when_unconstrained() -> None:
    model = Model(id="reasoning-model", name="Reasoning Model", provider="test", thinking=True)

    assert _section_for_model(model)._available_effort_levels() == list(ThinkingLevel)


def test_effort_levels_only_off_when_model_does_not_support_thinking() -> None:
    model = Model(id="plain-model", name="Plain Model", provider="test", thinking=False)

    assert _section_for_model(model)._available_effort_levels() == [ThinkingLevel.Off]
