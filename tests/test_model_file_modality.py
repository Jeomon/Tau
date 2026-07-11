"""Tests for File-modality curation in tau/builtins/models/text.py.

Modality.File is only meaningful where the provider layer actually has a
FileContent conversion branch (see test_provider_file_content.py): Anthropic
(+ Vertex, Claude Code), Gemini (generate, Vertex, Antigravity), and OpenAI
Codex Responses. Every other provider's model list must NOT claim File
support, since it would silently be dropped on the wire.
"""

from __future__ import annotations

from tau.builtins.models.text import models
from tau.inference.model.types import Modality

_FILE_CAPABLE_PROVIDERS = {
    "anthropic",
    "anthropic-vertex",
    "anthropic-claude-code",
    "google",
    "google-vertex",
    "google-antigravity",
    "openai-codex",
}

# A representative sample of providers whose wire format has no document/file
# block at all (see providers left untouched in test_provider_file_content.py).
_NON_FILE_CAPABLE_PROVIDERS_PRESENT = {
    "mistral",
    "ollama",
    "groq",
    "github-copilot",
    "openai-vertex",
}


def _models_by_provider(provider: str) -> list:
    return [m for m in models if m.provider == provider]


class TestFileCapableProviders:
    def test_every_file_capable_provider_has_at_least_one_model(self):
        # Guards against a provider ID typo silently making a whole block a no-op.
        for provider in _FILE_CAPABLE_PROVIDERS:
            assert _models_by_provider(provider), f"no models found for provider {provider!r}"

    def test_all_models_of_file_capable_providers_declare_file(self):
        for provider in _FILE_CAPABLE_PROVIDERS:
            for m in _models_by_provider(provider):
                assert Modality.File in m.input, f"{m.id} ({provider}) is missing Modality.File"

    def test_file_capable_models_also_declare_image(self):
        # Every File-capable model reached that constant by extending an
        # Image-supporting list — not a hard protocol requirement, just
        # documents the actual curation as done.
        for provider in _FILE_CAPABLE_PROVIDERS:
            for m in _models_by_provider(provider):
                assert Modality.Image in m.input, f"{m.id} ({provider}) lost Modality.Image"


class TestNonFileCapableProvidersUnaffected:
    def test_present_non_file_providers_have_no_file_modality(self):
        for provider in _NON_FILE_CAPABLE_PROVIDERS_PRESENT:
            provider_models = _models_by_provider(provider)
            assert provider_models, f"no models found for provider {provider!r}"
            for m in provider_models:
                assert Modality.File not in m.input, (
                    f"{m.id} ({provider}) unexpectedly declares Modality.File — "
                    "does this provider's conversion function now handle FileContent?"
                )

    def test_no_untracked_provider_declares_file(self):
        """Catch a new model accidentally getting File support without a
        matching provider-layer FileContent branch (see test_provider_file_content.py).
        """
        unexpected = sorted(
            {m.provider for m in models if Modality.File in m.input} - _FILE_CAPABLE_PROVIDERS
        )
        assert unexpected == []


class TestSpecificModelsSanityCheck:
    """Spot-checks a few well-known model ids directly, independent of the
    provider-set logic above, so a bug in the set comparisons themselves
    can't hide a real regression.
    """

    def _find(self, model_id: str, provider: str):
        for m in models:
            if m.id == model_id and m.provider == provider:
                return m
        raise AssertionError(f"model {model_id!r} ({provider}) not found")

    def test_claude_sonnet_5_has_file(self):
        assert Modality.File in self._find("claude-sonnet-5", "anthropic").input

    def test_gemini_2_5_pro_has_file(self):
        assert Modality.File in self._find("gemini-2.5-pro", "google").input

    def test_gpt_5_5_codex_has_file(self):
        assert Modality.File in self._find("gpt-5.5", "openai-codex").input

    def test_gemini_3_flash_antigravity_has_file(self):
        assert Modality.File in self._find("gemini-3-flash", "google-antigravity").input
