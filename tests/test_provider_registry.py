"""Tests for tau/inference/provider/registry.py — provider registries."""

from __future__ import annotations

from tau.inference.provider.registry import (
    AudioProviderRegistry,
    ImageProviderRegistry,
    ProviderRegistry,
    TextProviderRegistry,
    VideoProviderRegistry,
)
from tau.inference.provider.types import APIProvider, AudioProvider, ImageProvider, VideoProvider
from tau.inference.types import LLMOptions


def _api_provider(id: str = "test", name: str = "Test") -> APIProvider:
    return APIProvider(id=id, name=name, api="tau.inference.api.text.base", options=LLMOptions())


def _image_provider(id: str = "imgprov", name: str | None = None) -> ImageProvider:
    return ImageProvider(id=id, name=name or id, api="some.api", base_url="https://img.api.com")


def _audio_provider(id: str = "audprov", name: str | None = None) -> AudioProvider:
    return AudioProvider(id=id, name=name or id, api="some.api")


def _video_provider(id: str = "vidprov", name: str | None = None) -> VideoProvider:
    return VideoProvider(id=id, name=name or id, api="some.api")


class TestTextProviderRegistry:
    def test_register_and_get(self):
        r = TextProviderRegistry()
        p = _api_provider("anthropic")
        r.register(p)
        assert r.get("anthropic") is p

    def test_get_unknown_returns_none(self):
        r = TextProviderRegistry()
        assert r.get("unknown") is None

    def test_list_all(self):
        r = TextProviderRegistry()
        r.register(_api_provider("a"))
        r.register(_api_provider("b"))
        assert len(r.list()) == 2

    def test_unregister_removes_provider(self):
        r = TextProviderRegistry()
        r.register(_api_provider("prov"))
        r.unregister("prov")
        assert r.get("prov") is None

    def test_reset_clears_all(self):
        r = TextProviderRegistry()
        r.register(_api_provider("a"))
        r.register(_api_provider("b"))
        r.reset()
        assert r.list() == []

    def test_get_api_providers(self):
        r = TextProviderRegistry()
        r.register(_api_provider("prov"))
        assert len(r.get_api_providers()) == 1

    def test_get_oauth_providers_empty(self):
        r = TextProviderRegistry()
        r.register(_api_provider("prov"))
        assert r.get_oauth_providers() == []

    def test_get_api_provider_by_name(self):
        r = TextProviderRegistry()
        p = _api_provider("openai")
        r.register(p)
        assert r.get_api_provider("openai") is p

    def test_get_api_provider_returns_none_for_oauth(self):
        r = TextProviderRegistry()
        assert r.get_oauth_provider("nonexistent") is None


class TestImageProviderRegistry:
    def test_register_and_get(self):
        r = ImageProviderRegistry()
        p = _image_provider("dalle")
        r.register(p)
        assert r.get("dalle") is p

    def test_list_empty(self):
        r = ImageProviderRegistry()
        assert r.list() == []

    def test_unregister(self):
        r = ImageProviderRegistry()
        r.register(_image_provider("dalle"))
        r.unregister("dalle")
        assert r.get("dalle") is None


class TestAudioProviderRegistry:
    def test_register_and_list(self):
        r = AudioProviderRegistry()
        r.register(_audio_provider("whisper"))
        assert len(r.list()) == 1


class TestVideoProviderRegistry:
    def test_register_and_get(self):
        r = VideoProviderRegistry()
        r.register(_video_provider("fal"))
        assert r.get("fal") is not None


class TestProviderRegistry:
    def test_default_construction(self):
        r = ProviderRegistry()
        assert isinstance(r.text, TextProviderRegistry)
        assert isinstance(r.image, ImageProviderRegistry)
        assert isinstance(r.audio, AudioProviderRegistry)
        assert isinstance(r.video, VideoProviderRegistry)

    def test_text_providers_accessible(self):
        r = ProviderRegistry()
        r.text.register(_api_provider("prov"))
        assert r.text.get("prov") is not None

    def test_image_providers_accessible(self):
        r = ProviderRegistry()
        r.image.register(_image_provider("dalle"))
        assert r.image.get("dalle") is not None

    def test_custom_sub_registries(self):
        text_reg = TextProviderRegistry()
        r = ProviderRegistry(text=text_reg)
        assert r.text is text_reg
