"""Tests for tau/inference/model/registry.py — ModelRegistry."""

from __future__ import annotations

from tau.inference.model.registry import ModelRegistry
from tau.inference.model.types import Cost, Model


def _model(id: str, provider: str = "test_provider", name: str | None = None) -> Model:
    return Model(id=id, name=name or id, provider=provider, cost=Cost())


class TestModelRegistryRegister:
    def test_register_and_get(self):
        r = ModelRegistry()
        m = _model("claude-3")
        r.register(m)
        assert r.get("claude-3") is m

    def test_multiple_providers_same_id(self):
        r = ModelRegistry()
        m1 = _model("gpt-4", provider="openai")
        m2 = _model("gpt-4", provider="azure")
        r.register(m1)
        r.register(m2)
        assert len(r.list()) == 2

    def test_get_first_provider_wins(self):
        r = ModelRegistry()
        m1 = _model("gpt-4", provider="openai")
        m2 = _model("gpt-4", provider="azure")
        r.register(m1)
        r.register(m2)
        assert r.get("gpt-4") is m1

    def test_get_by_provider(self):
        r = ModelRegistry()
        m1 = _model("gpt-4", provider="openai")
        m2 = _model("gpt-4", provider="azure")
        r.register(m1)
        r.register(m2)
        assert r.get("gpt-4", provider="azure") is m2

    def test_get_unknown_returns_none(self):
        r = ModelRegistry()
        assert r.get("nonexistent") is None

    def test_get_unknown_provider_returns_none(self):
        r = ModelRegistry()
        r.register(_model("gpt-4", provider="openai"))
        assert r.get("gpt-4", provider="google") is None


class TestModelRegistryUnregister:
    def test_unregister_removes_all_providers(self):
        r = ModelRegistry()
        r.register(_model("gpt-4", provider="openai"))
        r.register(_model("gpt-4", provider="azure"))
        r.unregister("gpt-4")
        assert r.get("gpt-4") is None

    def test_unregister_specific_provider(self):
        r = ModelRegistry()
        m1 = _model("gpt-4", provider="openai")
        m2 = _model("gpt-4", provider="azure")
        r.register(m1)
        r.register(m2)
        r.unregister("gpt-4", provider="openai")
        assert r.get("gpt-4") is m2
        assert r.get("gpt-4", provider="openai") is None

    def test_unregister_nonexistent_is_noop(self):
        r = ModelRegistry()
        r.unregister("nonexistent")  # should not raise

    def test_unregister_removes_key_when_all_providers_gone(self):
        r = ModelRegistry()
        r.register(_model("gpt-4", provider="openai"))
        r.unregister("gpt-4", provider="openai")
        assert "gpt-4" not in r._models


class TestModelRegistryUnregisterByProvider:
    def test_removes_all_models_from_provider(self):
        r = ModelRegistry()
        r.register(_model("model-a", provider="prov1"))
        r.register(_model("model-b", provider="prov1"))
        r.register(_model("model-c", provider="prov2"))
        r.unregister_by_provider("prov1")
        assert r.get("model-a") is None
        assert r.get("model-b") is None
        assert r.get("model-c") is not None

    def test_unregister_by_provider_noop_for_unknown(self):
        r = ModelRegistry()
        r.register(_model("x", provider="prov"))
        r.unregister_by_provider("unknown_provider")
        assert r.get("x") is not None

    def test_mixed_provider_model_survives(self):
        r = ModelRegistry()
        m_keep = _model("shared", provider="prov2")
        r.register(_model("shared", provider="prov1"))
        r.register(m_keep)
        r.unregister_by_provider("prov1")
        assert r.get("shared") is m_keep


class TestModelRegistryList:
    def test_list_empty(self):
        r = ModelRegistry()
        assert r.list() == []

    def test_list_all_variants(self):
        r = ModelRegistry()
        r.register(_model("a"))
        r.register(_model("b"))
        r.register(_model("a", provider="other"))
        assert len(r.list()) == 3

    def test_reset_clears_all(self):
        r = ModelRegistry()
        r.register(_model("a"))
        r.register(_model("b"))
        r.reset()
        assert r.list() == []


class TestModelRegistryFromBuiltins:
    def test_from_text_builtins_has_models(self):
        r = ModelRegistry.from_text_builtins()
        assert len(r.list()) > 0

    def test_from_text_builtins_all_have_id(self):
        r = ModelRegistry.from_text_builtins()
        for m in r.list():
            assert m.id
            assert m.provider
