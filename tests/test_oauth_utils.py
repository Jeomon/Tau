"""Tests for tau/inference/provider/oauth/utils.py — parse_authorization_input."""

from __future__ import annotations

from tau.inference.provider.oauth.utils import parse_authorization_input


class TestParseAuthorizationInputEmpty:
    def test_empty_string_returns_none_none(self):
        code, state = parse_authorization_input("")
        assert code is None
        assert state is None

    def test_whitespace_only_returns_none_none(self):
        code, state = parse_authorization_input("   ")
        assert code is None
        assert state is None


class TestParseAuthorizationInputRedirectUrl:
    def test_full_redirect_url_with_code_and_state(self):
        url = "http://localhost:8080/callback?code=abc123&state=xyz"
        code, state = parse_authorization_input(url)
        assert code == "abc123"
        assert state == "xyz"

    def test_https_redirect_url(self):
        url = "https://example.com/callback?code=mycode&state=mystate"
        code, state = parse_authorization_input(url)
        assert code == "mycode"
        assert state == "mystate"

    def test_redirect_url_code_only(self):
        url = "http://localhost/cb?code=only_code"
        code, state = parse_authorization_input(url)
        assert code == "only_code"
        assert state is None

    def test_redirect_url_no_code(self):
        url = "http://localhost/cb?state=somestate"
        code, state = parse_authorization_input(url)
        assert code is None
        assert state == "somestate"


class TestParseAuthorizationInputHashDelimited:
    def test_hash_splits_code_and_state(self):
        code, state = parse_authorization_input("mycode#mystate")
        assert code == "mycode"
        assert state == "mystate"

    def test_hash_code_only(self):
        code, state = parse_authorization_input("mycode#")
        assert code == "mycode"
        assert state is None

    def test_hash_state_only(self):
        code, state = parse_authorization_input("#mystate")
        assert code is None
        assert state == "mystate"

    def test_hash_takes_first_split(self):
        code, state = parse_authorization_input("a#b#c")
        assert code == "a"
        assert state == "b#c"


class TestParseAuthorizationInputQueryString:
    def test_raw_query_string_with_code_and_state(self):
        code, state = parse_authorization_input("code=abc&state=xyz")
        assert code == "abc"
        assert state == "xyz"

    def test_raw_query_code_only(self):
        code, state = parse_authorization_input("code=justcode")
        assert code == "justcode"
        assert state is None


class TestParseAuthorizationInputBareCode:
    def test_bare_code_returns_code_no_state(self):
        code, state = parse_authorization_input("baretoken12345")
        assert code == "baretoken12345"
        assert state is None

    def test_strips_whitespace(self):
        code, state = parse_authorization_input("  mytoken  ")
        assert code == "mytoken"
        assert state is None
