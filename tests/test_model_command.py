from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from tau.inference.model.types import Modality, Model
from tau.modes.interactive.commands import model as model_command


def _tts_model(*, voices: list[str]) -> Model:
    return Model(
        id="tts-1",
        name="TTS-1",
        provider="openai",
        input=[Modality.Text],
        output=[Modality.Audio],
        voices=voices,
    )


def test_speak_model_with_voices_opens_voice_selector(monkeypatch) -> None:
    tts_model = _tts_model(voices=["alloy", "coral"])
    layout = SimpleNamespace(
        open_model_selector=Mock(),
        open_voice_selector=Mock(),
    )
    settings = SimpleNamespace(get_model_ref=Mock(return_value=None))
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(agent=None, settings_manager=settings),
        layout=layout,
        notify=Mock(),
    )
    monkeypatch.setattr(
        model_command,
        "_list_for",
        lambda modality: [tts_model] if modality == "speak" else [],
    )

    model_command.open_model_selector(ctx)
    model_commit = layout.open_model_selector.call_args.args[1]
    model_commit(("tts-1", "openai", "speak"))

    layout.open_voice_selector.assert_called_once()
    call = layout.open_voice_selector.call_args
    assert call.args[:3] == ("TTS-1", ["alloy", "coral"], None)


def test_apply_speak_model_persists_selected_voice() -> None:
    settings = SimpleNamespace(set_model_ref=Mock())
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(settings_manager=settings),
        notify=Mock(),
    )

    asyncio.run(
        model_command._apply_model(
            ctx,
            "speak",
            "tts-1",
            "openai",
            voice="coral",
        )
    )

    settings.set_model_ref.assert_called_once_with(
        "speak",
        "openai",
        "tts-1",
        voice="coral",
    )
    ctx.notify.assert_called_once_with("Speak model set to openai/tts-1 with voice coral")
