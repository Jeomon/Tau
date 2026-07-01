"""Tests for tau/utils/http_proxy.py — proxy resolution logic."""

from __future__ import annotations

import pytest

from tau.utils.http_proxy import (
    _get_proxy_env,
    _parse_proxy_target_url,
    _should_proxy_hostname,
    _validate_proxy_url,
    get_proxies_for_client,
    get_proxy_url_for_target,
)


class TestGetProxyEnv:
    def test_reads_lowercase(self, monkeypatch):
        monkeypatch.setenv("http_proxy", "http://low.proxy:8080")
        assert _get_proxy_env("http_proxy") == "http://low.proxy:8080"

    def test_reads_uppercase(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://up.proxy:3128")
        assert _get_proxy_env("HTTP_PROXY") == "http://up.proxy:3128"

    def test_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        assert _get_proxy_env("http_proxy") == ""


class TestParseProxyTargetUrl:
    def test_https_with_default_port(self):
        result = _parse_proxy_target_url("https://api.anthropic.com/v1")
        assert result == ("https", "api.anthropic.com", 443)

    def test_http_with_default_port(self):
        result = _parse_proxy_target_url("http://example.com/path")
        assert result == ("http", "example.com", 80)

    def test_explicit_port(self):
        result = _parse_proxy_target_url("https://example.com:9000/resource")
        assert result == ("https", "example.com", 9000)

    def test_invalid_url_returns_none(self):
        assert _parse_proxy_target_url("not-a-url") is None

    def test_empty_string_returns_none(self):
        assert _parse_proxy_target_url("") is None

    def test_ws_default_port(self):
        result = _parse_proxy_target_url("ws://stream.example.com")
        assert result == ("ws", "stream.example.com", 80)


class TestShouldProxyHostname:
    def test_empty_no_proxy_allows_all(self):
        assert _should_proxy_hostname("api.openai.com", 443, no_proxy="") is True

    def test_wildcard_star_blocks_all(self):
        assert _should_proxy_hostname("anything.com", 80, no_proxy="*") is False

    def test_exact_hostname_excluded(self):
        assert _should_proxy_hostname("localhost", 80, no_proxy="localhost") is False

    def test_unrelated_hostname_proxied(self):
        assert (
            _should_proxy_hostname("api.example.com", 443, no_proxy="localhost,127.0.0.1") is True
        )

    def test_wildcard_subdomain_excluded(self):
        assert _should_proxy_hostname("sub.corp.internal", 443, no_proxy="*.corp.internal") is False

    def test_wildcard_does_not_match_parent(self):
        assert _should_proxy_hostname("corp.internal", 443, no_proxy="*.corp.internal") is True

    def test_port_specific_exclusion_matches(self):
        assert _should_proxy_hostname("myhost", 8080, no_proxy="myhost:8080") is False

    def test_port_specific_exclusion_no_match_on_different_port(self):
        assert _should_proxy_hostname("myhost", 443, no_proxy="myhost:8080") is True

    def test_multiple_entries_comma_separated(self):
        assert (
            _should_proxy_hostname(
                "internal.corp", 80, no_proxy="localhost,internal.corp,127.0.0.1"
            )
            is False
        )


class TestValidateProxyUrl:
    def test_http_proxy_valid(self):
        _validate_proxy_url("http://proxy.company.com:3128")  # should not raise

    def test_https_proxy_valid(self):
        _validate_proxy_url("https://secure.proxy.com:8080")  # should not raise

    def test_socks_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _validate_proxy_url("socks5://proxy.example.com:1080")

    def test_ftp_raises(self):
        with pytest.raises(ValueError):
            _validate_proxy_url("ftp://proxy.example.com")


class TestGetProxyUrlForTarget:
    def test_returns_none_when_no_proxy_set(self, monkeypatch):
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("all_proxy", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        assert get_proxy_url_for_target("https://api.anthropic.com") is None

    def test_returns_proxy_from_https_env(self, monkeypatch):
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setenv("https_proxy", "http://corp.proxy:3128")
        result = get_proxy_url_for_target("https://api.anthropic.com")
        assert result == "http://corp.proxy:3128"

    def test_returns_proxy_from_http_env(self, monkeypatch):
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setenv("http_proxy", "http://corp.proxy:3128")
        result = get_proxy_url_for_target("http://api.example.com")
        assert result == "http://corp.proxy:3128"

    def test_no_proxy_excludes_host(self, monkeypatch):
        monkeypatch.setenv("https_proxy", "http://corp.proxy:3128")
        monkeypatch.setenv("no_proxy", "api.anthropic.com")
        result = get_proxy_url_for_target("https://api.anthropic.com")
        assert result is None

    def test_invalid_url_returns_none(self, monkeypatch):
        result = get_proxy_url_for_target("not-a-url")
        assert result is None

    def test_scheme_added_when_missing(self, monkeypatch):
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setenv("https_proxy", "corp.proxy:3128")
        result = get_proxy_url_for_target("https://api.example.com")
        assert result == "https://corp.proxy:3128"

    def test_all_proxy_fallback(self, monkeypatch):
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.setenv("all_proxy", "http://fallback.proxy:8888")
        result = get_proxy_url_for_target("https://api.example.com")
        assert result == "http://fallback.proxy:8888"


class TestGetProxiesForClient:
    def test_returns_none_when_no_proxy(self, monkeypatch):
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("all_proxy", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        assert get_proxies_for_client("https://api.anthropic.com") is None

    def test_returns_dict_with_both_schemes(self, monkeypatch):
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setenv("https_proxy", "http://proxy:3128")
        result = get_proxies_for_client("https://api.example.com")
        assert result is not None
        assert "http://" in result
        assert "https://" in result
