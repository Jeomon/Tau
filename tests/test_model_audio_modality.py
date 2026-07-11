"""Tests for Audio-modality curation in tau/builtins/models/text.py.

Modality.Audio is only meaningful where the provider layer actually has an
AudioContent conversion branch (see test_provider_audio_content.py) —
exclusively Gemini (generate, Vertex, Antigravity). Anthropic has no audio
input support in its API at all, and the OpenAI Codex/Responses models Tau
uses explicitly list audio as unsupported (gpt-audio/gpt-audio-mini are a
separate product line) — so neither should ever claim Modality.Audio.
"""

from __future__ import annotations

from tau.builtins.models.text import models
from tau.inference.model.types import Modality

_AUDIO_CAPABLE_PROVIDERS = {"google", "google-vertex", "google-antigravity"}

# Providers that must NOT have audio, including the two other File-capable
# families (Anthropic, OpenAI Codex) to make the distinction explicit — File
# support does not imply Audio support.
_NON_AUDIO_CAPABLE_PROVIDERS_PRESENT = {
    "anthropic",
    "anthropic-vertex",
    "anthropic-claude-code",
    "openai-codex",
    "mistral",
    "ollama",
    "groq",
}


def _models_by_provider(provider: str) -> list:
    return [m for m in models if m.provider == provider]


class TestAudioCapableProviders:
    def test_every_audio_capable_provider_has_at_least_one_model(self):
        for provider in _AUDIO_CAPABLE_PROVIDERS:
            assert _models_by_provider(provider), f"no models found for provider {provider!r}"

    def test_all_gemini_models_declare_audio(self):
        # "google" and "google-vertex" are pure-Gemini provider blocks — every
        # model there should have Audio.
        for provider in ("google", "google-vertex"):
            for m in _models_by_provider(provider):
                assert Modality.Audio in m.input, f"{m.id} ({provider}) is missing Modality.Audio"

    def test_gemini_named_antigravity_models_declare_audio(self):
        # google-antigravity is a mixed block (real Gemini models proxied
        # alongside Claude models on the same gateway) — only the genuinely
        # Gemini-named entries should have Audio, not the whole provider.
        for m in _models_by_provider("google-antigravity"):
            if m.id.startswith("gemini"):
                assert Modality.Audio in m.input, f"{m.id} (google-antigravity) is missing Audio"


class TestNonAudioCapableProvidersUnaffected:
    def test_present_non_audio_providers_have_no_audio_modality(self):
        for provider in _NON_AUDIO_CAPABLE_PROVIDERS_PRESENT:
            provider_models = _models_by_provider(provider)
            assert provider_models, f"no models found for provider {provider!r}"
            for m in provider_models:
                assert Modality.Audio not in m.input, (
                    f"{m.id} ({provider}) unexpectedly declares Modality.Audio — "
                    "does this provider's conversion function now handle AudioContent?"
                )

    def test_no_untracked_provider_declares_audio(self):
        """Catch a new model accidentally getting Audio support without a
        matching provider-layer AudioContent branch (see test_provider_audio_content.py).

        "openrouter" is a known, pre-existing exception (predates this
        session's changes): dozens of its models — including proxied Gemini
        models — already claim Modality.Audio, but openrouter shares
        openai_messages_to_chat with the rest of the Chat-Completions family,
        which has zero AudioContent handling. That's a real, separate gap
        (audio silently dropped for those models today), out of scope here —
        this test only guards the providers this session actually touched.
        """
        known_pre_existing_gap = {"openrouter"}
        unexpected = sorted(
            {m.provider for m in models if Modality.Audio in m.input}
            - _AUDIO_CAPABLE_PROVIDERS
            - known_pre_existing_gap
        )
        assert unexpected == []

    def test_file_capable_does_not_imply_audio_capable(self):
        # Anthropic and OpenAI Codex both got Modality.File; neither should
        # have picked up Modality.Audio as a side effect of that curation pass.
        for provider in ("anthropic", "anthropic-vertex", "anthropic-claude-code", "openai-codex"):
            for m in _models_by_provider(provider):
                assert Modality.File in m.input, f"{m.id} ({provider}) should still have File"
                assert Modality.Audio not in m.input, f"{m.id} ({provider}) should not have Audio"


class TestSpecificModelsSanityCheck:
    """Spot-checks specific model ids directly, independent of the
    provider-set logic above, so a bug in the set comparisons themselves
    can't hide a real regression.
    """

    def _find(self, model_id: str, provider: str):
        for m in models:
            if m.id == model_id and m.provider == provider:
                return m
        raise AssertionError(f"model {model_id!r} ({provider}) not found")

    def test_gemini_2_5_flash_has_audio(self):
        assert Modality.Audio in self._find("gemini-2.5-flash", "google").input

    def test_gemini_2_5_flash_antigravity_has_audio(self):
        m = self._find("gemini-2.5-flash", "google-antigravity")
        assert Modality.Audio in m.input

    def test_claude_sonnet_4_6_antigravity_has_no_audio(self):
        # The Claude model proxied through the antigravity gateway must not
        # inherit Audio just because it's in the same provider block as Gemini.
        m = self._find("claude-sonnet-4-6", "google-antigravity")
        assert Modality.Audio not in m.input

    def test_claude_sonnet_5_anthropic_has_no_audio(self):
        assert Modality.Audio not in self._find("claude-sonnet-5", "anthropic").input

    def test_gpt_5_5_codex_has_no_audio(self):
        assert Modality.Audio not in self._find("gpt-5.5", "openai-codex").input
