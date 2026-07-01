"""Tests for tau/utils/secrets.py — secret reference resolution."""

from __future__ import annotations

import pytest

from tau.utils.secrets import _cache, clear_cache, resolve_secret, resolve_secrets


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class TestResolveSecret:
    def test_literal_value_returned_as_is(self):
        assert resolve_secret("my-api-key") == "my-api-key"

    def test_none_returns_empty(self):
        assert resolve_secret(None) == ""

    def test_empty_string_returns_empty(self):
        assert resolve_secret("") == ""

    def test_env_var_resolved(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET_KEY", "secret-value")
        assert resolve_secret("$MY_SECRET_KEY") == "secret-value"

    def test_env_var_unset_returns_empty(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert resolve_secret("$UNSET_VAR_XYZ") == ""

    def test_shell_command_resolved(self):
        result = resolve_secret("!echo hello")
        assert result == "hello"

    def test_shell_command_output_stripped(self):
        result = resolve_secret("!printf '  trimmed  '")
        assert result == "trimmed"

    def test_literal_cached_on_second_call(self):
        clear_cache()
        resolve_secret("cached-key")
        assert "cached-key" in _cache

    def test_env_var_cached(self, monkeypatch):
        clear_cache()
        monkeypatch.setenv("CACHE_TEST_KEY", "val")
        resolve_secret("$CACHE_TEST_KEY")
        assert "$CACHE_TEST_KEY" in _cache

    def test_failed_resolution_not_cached(self, monkeypatch):
        clear_cache()
        monkeypatch.delenv("NOTSET_KEY", raising=False)
        resolve_secret("$NOTSET_KEY")
        assert "$NOTSET_KEY" not in _cache

    def test_clear_cache_removes_entries(self, monkeypatch):
        monkeypatch.setenv("CLEAR_TEST", "v")
        resolve_secret("$CLEAR_TEST")
        clear_cache()
        assert len(_cache) == 0


class TestResolveSecrets:
    def test_empty_dict_returns_empty(self):
        assert resolve_secrets({}) == {}

    def test_none_returns_empty(self):
        assert resolve_secrets(None) == {}

    def test_resolves_each_value(self, monkeypatch):
        monkeypatch.setenv("HDR_KEY", "Bearer token123")
        result = resolve_secrets({"Authorization": "$HDR_KEY", "X-Custom": "literal"})
        assert result["Authorization"] == "Bearer token123"
        assert result["X-Custom"] == "literal"

    def test_keys_preserved(self, monkeypatch):
        monkeypatch.setenv("K1", "v1")
        result = resolve_secrets({"a": "$K1", "b": "plain"})
        assert set(result.keys()) == {"a", "b"}
