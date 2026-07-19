"""Tests for the models.dev dynamic model catalog (Catalog class)."""

from __future__ import annotations

import json
import time

import pytest

from tau.inference.model import catalog as catalog_mod
from tau.inference.model.catalog import Catalog, _to_text_model
from tau.inference.model.types import Cost, Modality
from tau.inference.types import ThinkingLevel


def _entry(**overrides) -> dict:
    base = {
        "id": "test-model-1",
        "name": "Test Model 1",
        "cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
        "limit": {"context": 200_000, "output": 64_000},
        "modalities": {"input": ["text", "image"], "output": ["text"]},
        "reasoning": True,
        "tool_call": True,
    }
    base.update(overrides)
    return base


def _payload(dev_provider: str = "anthropic", models: dict | None = None) -> dict:
    if models is None:
        models = {"test-model-1": _entry()}
    return {dev_provider: {"id": dev_provider, "models": models}}


@pytest.fixture
def cat(tmp_path) -> Catalog:
    return Catalog(path=tmp_path / "models-catalog.json")


def _loaded(cat: Catalog, data: dict) -> Catalog:
    cat._data = data
    cat._fetched_at = time.time()
    return cat


class TestToTextModel:
    def test_maps_core_fields(self):
        model = _to_text_model("anthropic", _entry())
        assert model is not None
        assert model.id == "test-model-1"
        assert model.name == "Test Model 1"
        assert model.provider == "anthropic"
        assert model.cost == Cost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
        assert model.context_window == 200_000
        assert model.max_output_tokens == 64_000
        assert model.thinking is True
        assert model.input == [Modality.Text, Modality.Image]
        assert model.output == [Modality.Text]

    def test_defaults_for_missing_fields(self):
        model = _to_text_model("openai", {"id": "bare"})
        assert model is not None
        assert model.name == "bare"
        assert model.cost == Cost()
        assert model.context_window == 0
        assert model.max_output_tokens == 16384
        assert model.max_input_tokens is None
        assert model.thinking is False
        assert model.input == [Modality.Text]

    def test_rejects_missing_id(self):
        assert _to_text_model("openai", {"name": "no id"}) is None

    def test_rejects_deprecated(self):
        assert _to_text_model("openai", _entry(status="deprecated")) is None
        assert _to_text_model("openai", _entry(status="beta")) is not None

    def test_rejects_non_text_output(self):
        # Image/video generators take a text prompt but can't chat. models.dev
        # marks many as also emitting text (output: ["text", "image"]) — the
        # text registry requires text-only output.
        image_gen = _entry(modalities={"input": ["text", "image"], "output": ["image"]})
        video_gen = _entry(modalities={"input": ["text"], "output": ["video"]})
        mixed = _entry(modalities={"input": ["text", "image"], "output": ["text", "image"]})
        tts = _entry(modalities={"input": ["text"], "output": ["audio"]})
        assert _to_text_model("openai", image_gen) is None
        assert _to_text_model("xai", video_gen) is None
        assert _to_text_model("openai", mixed) is None
        assert _to_text_model("google", tts) is None

    def test_missing_output_modalities_assumed_text(self):
        assert _to_text_model("openai", _entry(modalities={"input": ["text"]})) is not None

    def test_rejects_non_text_input(self):
        stt = _entry(modalities={"input": ["audio"], "output": ["text"]})
        assert _to_text_model("openai", stt) is None

    def test_pdf_maps_to_file_for_anthropic_only(self):
        mods = {"input": ["text", "image", "pdf"], "output": ["text"]}
        anthropic = _to_text_model("anthropic", _entry(modalities=mods))
        openai = _to_text_model("openai", _entry(modalities=mods))
        assert anthropic is not None and openai is not None
        assert Modality.File in anthropic.input
        assert Modality.File not in openai.input

    def test_audio_video_kept_for_google_only(self):
        mods = {"input": ["text", "audio", "video"], "output": ["text"]}
        google = _to_text_model("google", _entry(modalities=mods))
        mistral = _to_text_model("mistral", _entry(modalities=mods))
        assert google is not None and mistral is not None
        assert Modality.Audio in google.input and Modality.Video in google.input
        assert Modality.Audio not in mistral.input and Modality.Video not in mistral.input

    def test_unknown_modality_names_ignored(self):
        entry = _entry(modalities={"input": ["text", "smell"], "output": ["text"]})
        model = _to_text_model("openai", entry)
        assert model is not None
        assert model.input == [Modality.Text]

    def test_max_input_tokens_from_limit_input(self):
        entry = _entry(limit={"context": 500_000, "input": 372_000, "output": 128_000})
        model = _to_text_model("openai", entry)
        assert model is not None
        assert model.max_input_tokens == 372_000

    def test_max_input_tokens_ignored_when_not_a_real_bound(self):
        # input == context (stepfun-style) or input == 0 is not a prompt ceiling
        equal = _entry(limit={"context": 256_000, "input": 256_000, "output": 256_000})
        zero = _entry(limit={"context": 256_000, "input": 0, "output": 16_000})
        for entry in (equal, zero):
            model = _to_text_model("openai", entry)
            assert model is not None
            assert model.max_input_tokens is None


class TestThinkingLevels:
    def test_clean_effort_values_map_with_off_prepended(self):
        entry = _entry(
            reasoning_options=[{"type": "effort", "values": ["low", "medium", "high", "max"]}]
        )
        model = _to_text_model("anthropic", entry)
        assert model is not None
        assert model.thinking_levels == [
            ThinkingLevel.Off,
            ThinkingLevel.Low,
            ThinkingLevel.Medium,
            ThinkingLevel.High,
            ThinkingLevel.Max,
        ]

    def test_unknown_effort_value_yields_unconfirmed(self):
        entry = _entry(reasoning_options=[{"type": "effort", "values": ["low", "mystery"]}])
        model = _to_text_model("anthropic", entry)
        assert model is not None
        assert model.thinking_levels == []

    def test_toggle_and_budget_yield_unconfirmed(self):
        for option in ({"type": "toggle"}, {"type": "budget_tokens", "min": 1024}):
            model = _to_text_model("anthropic", _entry(reasoning_options=[option]))
            assert model is not None
            assert model.thinking is True
            assert model.thinking_levels == []

    def test_no_reasoning_means_no_levels(self):
        model = _to_text_model("anthropic", _entry(reasoning=False))
        assert model is not None
        assert model.thinking is False
        assert model.thinking_levels == []


class TestCatalogQueries:
    def test_text_models_maps_supported_providers_only(self, cat):
        _loaded(cat, _payload("anthropic") | _payload("some-unknown-provider"))
        assert [m.provider for m in cat.text_models()] == ["anthropic"]

    def test_provider_id_translation(self, cat):
        _loaded(cat, _payload("fireworks-ai") | _payload("moonshotai"))
        assert {m.provider for m in cat.text_models()} == {"fireworks", "kimi"}

    def test_text_models_filtered_to_one_provider(self, cat):
        _loaded(cat, _payload("anthropic") | _payload("moonshotai"))
        assert [m.provider for m in cat.text_models("kimi")] == ["kimi"]
        assert cat.text_models("bedrock") == []  # unmapped provider

    def test_skips_dated_duplicate_when_base_exists(self, cat):
        models = {
            "test-model-1": _entry(),
            "test-model-1-20250805": _entry(id="test-model-1-20250805"),
        }
        _loaded(cat, _payload("anthropic", models))
        assert [m.id for m in cat.text_models()] == ["test-model-1"]

    def test_keeps_dated_id_without_base(self, cat):
        _loaded(cat, _payload("anthropic", {"solo-20250805": _entry(id="solo-20250805")}))
        assert [m.id for m in cat.text_models()] == ["solo-20250805"]

    def test_malformed_entries_skipped(self, cat):
        _loaded(cat, _payload("anthropic", {"good": _entry(id="good"), "bad": "not-a-dict"}))
        assert [m.id for m in cat.text_models()] == ["good"]

    def test_get_text_model(self, cat):
        _loaded(cat, _payload("anthropic"))
        model = cat.get_text_model("test-model-1", "anthropic")
        assert model is not None and model.id == "test-model-1"
        assert cat.get_text_model("nope", "anthropic") is None
        assert cat.get_text_model("test-model-1", "bedrock") is None

    def test_get_image_model(self, cat):
        img = _entry(id="img-1", modalities={"input": ["text"], "output": ["image"]})
        _loaded(cat, _payload("openai", {"img-1": img}))
        model = cat.get_image_model("img-1", "openai")
        assert model is not None and model.output == [Modality.Image]
        # chat entries don't resolve through the image lookup, and vice versa
        assert cat.get_text_model("img-1", "openai") is None
        assert cat.get_image_model("img-1", "anthropic") is None  # not in image map

    def test_get_video_model(self, cat):
        vid = _entry(id="vid-1", modalities={"input": ["text"], "output": ["video"]})
        _loaded(cat, _payload("openrouter", {"vid-1": vid}))
        model = cat.get_video_model("vid-1", "openrouter")
        assert model is not None and model.output == [Modality.Video]
        assert cat.get_video_model("nope", "openrouter") is None

    def test_get_audio_model(self, cat):
        tts = _entry(id="say-1", modalities={"input": ["text"], "output": ["audio"]})
        stt = _entry(id="hear-1", modalities={"input": ["audio"], "output": ["text"]})
        _loaded(cat, _payload("groq", {"say-1": tts, "hear-1": stt}))
        say = cat.get_audio_model("say-1", "groq")
        hear = cat.get_audio_model("hear-1", "groq")
        assert say is not None and say.is_tts
        assert hear is not None and hear.is_stt
        assert cat.get_audio_model("say-1", "fireworks") is None  # not in audio map

    def test_empty_before_load(self, cat):
        assert cat.text_models() == []
        assert cat.get_text_model("x", "anthropic") is None


class TestMediaMappers:
    def test_image_generation_mapped(self):
        entry = _entry(
            id="img-gen",
            modalities={"input": ["text", "image"], "output": ["image", "text"]},
            cost={"input": 10, "output": 40},
        )
        model = catalog_mod._to_image_model("openai", entry)
        assert model is not None
        assert model.input == [Modality.Text, Modality.Image]
        assert model.output == [Modality.Image]
        assert model.cost.input == 10.0
        assert model.api is None  # inherits the provider's default adapter

    def test_image_requires_text_prompt(self):
        entry = _entry(modalities={"input": ["image"], "output": ["image"]})
        assert catalog_mod._to_image_model("openai", entry) is None

    def test_image_rejects_chat_and_deprecated(self):
        chat = _entry(modalities={"input": ["text"], "output": ["text"]})
        dep = _entry(
            status="deprecated", modalities={"input": ["text"], "output": ["image"]}
        )
        assert catalog_mod._to_image_model("openai", chat) is None
        assert catalog_mod._to_image_model("openai", dep) is None

    def test_video_generation_mapped(self):
        entry = _entry(id="vid", modalities={"input": ["text", "image"], "output": ["video"]})
        model = catalog_mod._to_video_model("openrouter", entry)
        assert model is not None
        assert model.input == [Modality.Text, Modality.Image]
        assert model.output == [Modality.Video]

    def test_tts_mapped(self):
        entry = _entry(id="say", modalities={"input": ["text"], "output": ["audio"]})
        model = catalog_mod._to_audio_model("google", entry)
        assert model is not None
        assert model.is_tts and not model.is_stt
        assert model.voices == []  # models.dev carries no voice lists

    def test_stt_mapped(self):
        entry = _entry(id="whisper-x", modalities={"input": ["audio"], "output": ["text"]})
        model = catalog_mod._to_audio_model("groq", entry)
        assert model is not None
        assert model.is_stt and not model.is_tts

    def test_audio_chat_models_excluded(self):
        # Gemini-style chat (audio+text in, text out) belongs to the text
        # registry, not the audio one.
        gemini_chat = _entry(
            modalities={"input": ["text", "image", "audio"], "output": ["text"]}
        )
        realtime = _entry(modalities={"input": ["audio", "text"], "output": ["audio", "text"]})
        assert catalog_mod._to_audio_model("google", gemini_chat) is None
        assert catalog_mod._to_audio_model("openai", realtime) is None

    def test_router_meta_models_excluded(self):
        router = _entry(
            id="openrouter/auto",
            family="auto",
            modalities={"input": ["text", "image", "audio"], "output": ["text", "image"]},
        )
        assert catalog_mod._to_image_model("openrouter", router) is None

    def test_modality_provider_maps_filter(self, cat):
        img = _entry(id="i1", modalities={"input": ["text"], "output": ["image"]})
        _loaded(cat, _payload("togetherai", {"i1": img}) | _payload("anthropic", {"i1": img}))
        # together is wired for image; anthropic is not in the image map
        assert [(m.provider, m.id) for m in cat.image_models()] == [("together", "i1")]
        # ...and an unmapped provider filter returns empty
        assert cat.video_models("anthropic") == []


class TestCacheRoundTrip:
    def test_save_load(self, cat):
        _loaded(cat, _payload("anthropic"))
        cat.save()
        fresh = Catalog(path=cat.path)
        assert fresh.load() is True
        assert fresh.data == cat.data
        assert fresh.fetched_at == cat.fetched_at

    def test_load_missing_file(self, cat):
        assert cat.load() is False

    def test_load_corrupt_file(self, cat):
        cat.path.write_text("{not json", encoding="utf-8")
        assert cat.load() is False

    def test_load_wrong_shape(self, cat):
        cat.path.write_text(json.dumps({"data": "nope"}), encoding="utf-8")
        assert cat.load() is False

    def test_fetch_trims_to_mapped_providers(self, cat, monkeypatch):
        full = _payload("anthropic") | _payload("frogbot")

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return full

        class _Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, headers=None):
                return _Resp()

        import httpx

        monkeypatch.setattr(httpx, "Client", _Client)
        cat.fetch()
        assert cat.data is not None and set(cat.data) == {"anthropic"}
        assert cat.path.exists()

    def test_is_stale(self, cat):
        now = time.time()
        assert cat.is_stale()  # nothing loaded
        _loaded(cat, _payload())
        assert not cat.is_stale(now=now)
        cat._fetched_at = now - 5 * 60 * 60
        assert cat.is_stale(now=now)
        cat._fetched_at = None
        assert cat.is_stale(now=now)


class TestRealPayloadShape:
    def test_handles_representative_snapshot(self, cat):
        # Trimmed real-world shape: experimental modes, reasoning_options,
        # tiered cost keys, and providers tau doesn't map.
        data = {
            "anthropic": {
                "id": "anthropic",
                "models": {
                    "claude-test": {
                        "id": "claude-test",
                        "name": "Claude Test",
                        "attachment": True,
                        "reasoning": True,
                        "reasoning_options": [
                            {"type": "effort", "values": ["low", "high", "max"]}
                        ],
                        "cost": {"input": 5, "output": 25, "cache_read": 0.5, "tiers": []},
                        "limit": {"context": 1_000_000, "output": 128_000},
                        "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                        "experimental": {"modes": {"fast": {}}},
                    }
                },
            },
            "frogbot": {"id": "frogbot", "models": {"frog-1": {"id": "frog-1"}}},
        }
        _loaded(cat, data)
        models = cat.text_models()
        assert len(models) == 1
        m = models[0]
        assert m.id == "claude-test"
        assert m.context_window == 1_000_000
        assert m.input == [Modality.Text, Modality.Image, Modality.File]
        assert m.thinking_levels == [
            ThinkingLevel.Off,
            ThinkingLevel.Low,
            ThinkingLevel.High,
            ThinkingLevel.Max,
        ]
