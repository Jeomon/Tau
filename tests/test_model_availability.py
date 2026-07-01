"""Tests for model availability filtering and STT/TTS classification (step 2)."""

from __future__ import annotations

from tau.inference.api.availability import available_models
from tau.inference.model.registry import ModelRegistry
from tau.inference.model.types import Cost, Modality, Model
from tau.inference.types import AuthType


def _model(id: str, provider: str, *, input=None, output=None) -> Model:
    return Model(
        id=id,
        name=id,
        provider=provider,
        cost=Cost(),
        input=input or [Modality.Text],
        output=output or [Modality.Text],
    )


class _FakeProvider:
    def __init__(self, auth_type: AuthType) -> None:
        self.auth_type = auth_type


class _FakeProviders:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get(self, key: str):
        return self._m.get(key)


class _FakeAuth:
    """Auth manager that never has a stored credential (env var is the only path)."""

    def reload(self) -> None:
        pass

    def get(self, provider_id: str):
        return None


class TestSTTTTSClassification:
    def test_stt_is_audio_in_text_out(self):
        m = _model("whisper", "openai", input=[Modality.Audio], output=[Modality.Text])
        assert m.is_stt is True
        assert m.is_tts is False

    def test_tts_is_text_in_audio_out(self):
        m = _model("tts-1", "openai", input=[Modality.Text], output=[Modality.Audio])
        assert m.is_tts is True
        assert m.is_stt is False

    def test_plain_text_model_is_neither(self):
        m = _model("gpt", "openai")
        assert m.is_stt is False
        assert m.is_tts is False

    def test_builtin_audio_models_split_cleanly(self):
        audio = ModelRegistry.from_audio_builtins().list()
        stt = {m.id for m in audio if m.is_stt}
        tts = {m.id for m in audio if m.is_tts}
        assert stt and tts  # both groups are non-empty
        assert not (stt & tts)  # no model is both
        # every audio model classifies as exactly one of the two
        assert all(m.is_stt ^ m.is_tts for m in audio)


class TestAvailableModels:
    def test_no_auth_type_provider_always_included(self):
        models = ModelRegistry()
        models.register(_model("local-llm", "ollama"))
        providers = _FakeProviders({"ollama": _FakeProvider(AuthType.None_)})
        result = available_models(models, providers, _FakeAuth())  # type: ignore[arg-type]
        assert [m.id for m in result] == ["local-llm"]

    def test_api_provider_excluded_without_credential_or_env(self, monkeypatch):
        monkeypatch.delenv("KEYPROV_API_KEY", raising=False)
        models = ModelRegistry()
        models.register(_model("paid", "keyprov"))
        providers = _FakeProviders({"keyprov": _FakeProvider(AuthType.ApiKey)})
        result = available_models(models, providers, _FakeAuth())  # type: ignore[arg-type]
        assert result == []

    def test_api_provider_included_via_env_var(self, monkeypatch):
        monkeypatch.setenv("KEYPROV_API_KEY", "sk-test")
        models = ModelRegistry()
        models.register(_model("paid", "keyprov"))
        providers = _FakeProviders({"keyprov": _FakeProvider(AuthType.ApiKey)})
        result = available_models(models, providers, _FakeAuth())  # type: ignore[arg-type]
        assert [m.id for m in result] == ["paid"]

    def test_unknown_provider_skipped(self):
        models = ModelRegistry()
        models.register(_model("orphan", "ghost"))
        result = available_models(models, _FakeProviders({}), _FakeAuth())  # type: ignore[arg-type]
        assert result == []

    def test_duplicate_provider_id_returned_once(self, monkeypatch):
        monkeypatch.delenv("FREE_API_KEY", raising=False)
        models = ModelRegistry()
        models.register(_model("dup", "free"))
        models.register(_model("dup", "free"))  # same provider/id
        providers = _FakeProviders({"free": _FakeProvider(AuthType.None_)})
        result = available_models(models, providers, _FakeAuth())  # type: ignore[arg-type]
        assert len(result) == 1


class TestFamilyListAvailable:
    def test_each_family_returns_a_list(self):
        from tau.inference.api.audio.service import AudioLLM
        from tau.inference.api.image.service import ImageLLM
        from tau.inference.api.text.service import TextLLM
        from tau.inference.api.video.service import VideoLLM

        for LLM in (TextLLM, AudioLLM, ImageLLM, VideoLLM):
            assert isinstance(LLM.list_available(), list)
