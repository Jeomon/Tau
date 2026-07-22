"""Tests for tau/auth/manager.py — credential CRUD and helper functions."""

from __future__ import annotations

from tau.auth.manager import AuthManager, _get_env_api_key, _is_unrecoverable_refresh_error
from tau.auth.types import APICredential, OAuthCredential
from tau.inference.provider.registry import ProviderRegistry


def _manager(initial: dict | None = None) -> AuthManager:
    return AuthManager.in_memory(ProviderRegistry(), initial or {})


# ---------------------------------------------------------------------------
# _get_env_api_key
# ---------------------------------------------------------------------------


class TestGetEnvApiKey:
    def test_reads_provider_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        assert _get_env_api_key("anthropic") == "sk-test-key"

    def test_returns_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("NOEXIST_API_KEY", raising=False)
        assert _get_env_api_key("noexist") is None

    def test_uppercases_provider(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert _get_env_api_key("openai") == "openai-key"


# ---------------------------------------------------------------------------
# _is_unrecoverable_refresh_error
# ---------------------------------------------------------------------------


class TestIsUnrecoverableRefreshError:
    def test_invalid_grant_is_unrecoverable(self):
        e = RuntimeError("Request failed (400): invalid_grant")
        assert _is_unrecoverable_refresh_error(e) is True

    def test_invalid_token_is_unrecoverable(self):
        e = RuntimeError("Request failed: invalid_token")
        assert _is_unrecoverable_refresh_error(e) is True

    def test_401_in_message_is_unrecoverable(self):
        e = RuntimeError("Request failed (401): unauthorized")
        assert _is_unrecoverable_refresh_error(e) is True

    def test_invalid_request_is_unrecoverable(self):
        e = RuntimeError("invalid_request: token missing")
        assert _is_unrecoverable_refresh_error(e) is True

    def test_auth_error_from_classify_is_unrecoverable(self):
        e = Exception("invalid api key provided")
        e.status_code = 401  # type: ignore[attr-defined]
        assert _is_unrecoverable_refresh_error(e) is True

    def test_rate_limit_is_recoverable(self):
        e = Exception("too many requests")
        e.status_code = 429  # type: ignore[attr-defined]
        assert _is_unrecoverable_refresh_error(e) is False

    def test_server_error_is_recoverable(self):
        e = Exception("internal server error")
        e.status_code = 500  # type: ignore[attr-defined]
        assert _is_unrecoverable_refresh_error(e) is False

    def test_timeout_is_recoverable(self):
        e = TimeoutError("timed out")
        assert _is_unrecoverable_refresh_error(e) is False


# ---------------------------------------------------------------------------
# AuthManager CRUD (in-memory)
# ---------------------------------------------------------------------------


class TestAuthManagerFactory:
    def test_creates_empty_manager(self):
        mgr = _manager()
        assert mgr.list() == []

    def test_drain_errors_empty_on_fresh(self):
        mgr = _manager()
        assert mgr.drain_errors() == []


class TestAuthManagerGetSetHasRemove:
    def test_set_and_get_api_credential(self):
        mgr = _manager()
        cred = APICredential(key="my-key")
        mgr.set("anthropic", cred)
        stored = mgr.get("anthropic")
        assert isinstance(stored, APICredential)
        assert stored.key == "my-key"

    def test_set_and_get_oauth_credential(self):
        mgr = _manager()
        cred = OAuthCredential(access="acc", refresh="ref", expires=9999)
        mgr.set("claude", cred)
        stored = mgr.get("claude")
        assert isinstance(stored, OAuthCredential)
        assert stored.access == "acc"

    def test_get_returns_none_for_unknown(self):
        mgr = _manager()
        assert mgr.get("unknown") is None

    def test_has_true_when_set(self):
        mgr = _manager()
        mgr.set("openai", APICredential(key="k"))
        assert mgr.has("openai") is True

    def test_has_false_when_not_set(self):
        mgr = _manager()
        assert mgr.has("openai") is False

    def test_list_returns_provider_names(self):
        mgr = _manager()
        mgr.set("anthropic", APICredential(key="a"))
        mgr.set("openai", APICredential(key="b"))
        providers = mgr.list()
        assert "anthropic" in providers
        assert "openai" in providers

    def test_remove_deletes_credential(self):
        mgr = _manager()
        mgr.set("anthropic", APICredential(key="k"))
        mgr.remove("anthropic")
        assert mgr.get("anthropic") is None
        assert mgr.has("anthropic") is False

    def test_remove_nonexistent_is_safe(self):
        mgr = _manager()
        mgr.remove("nope")  # should not raise


# ---------------------------------------------------------------------------
# AuthStatus
# ---------------------------------------------------------------------------


class TestAuthManagerAuthStatus:
    def test_configured_when_stored(self):
        mgr = _manager()
        mgr.set("anthropic", APICredential(key="k"))
        status = mgr.get_auth_status("anthropic")
        assert status.configured is True
        assert status.source == "stored"

    def test_not_configured_when_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mgr = _manager()
        status = mgr.get_auth_status("anthropic")
        assert status.configured is False

    def test_configured_from_env(self, monkeypatch):
        monkeypatch.setenv("MYCLOUD_API_KEY", "envkey")
        mgr = _manager()
        status = mgr.get_auth_status("mycloud")
        assert status.configured is True
        assert status.source == "env"

    def test_runtime_override_takes_precedence(self, monkeypatch):
        monkeypatch.delenv("MYPROV_API_KEY", raising=False)
        mgr = _manager()
        mgr.set_runtime_api_key("myprov", "runtime-key")
        status = mgr.get_auth_status("myprov")
        assert status.configured is True
        assert status.source == "runtime"

    def test_remove_runtime_api_key(self):
        mgr = _manager()
        mgr.set_runtime_api_key("prov", "k")
        mgr.remove_runtime_api_key("prov")
        assert "prov" not in mgr.runtime_overrides


# ---------------------------------------------------------------------------
# OAuth refresh backoff (get_api_key / force_refresh)
# ---------------------------------------------------------------------------


import time

import pytest

from tau.inference.provider.registry import TextProviderRegistry
from tau.inference.provider.types import OAuthProvider


class _FakeOAuthProvider(OAuthProvider):
    """OAuth provider whose refresh_token() replays a scripted result list."""

    def __init__(self, refresh_results):
        super().__init__(id="fake-oauth", name="Fake OAuth")
        self.refresh_results = list(refresh_results)
        self.refresh_calls = 0

    @property
    def api(self):
        return "fake"

    async def login(self, callbacks):
        raise NotImplementedError

    async def logout(self, credential):
        pass

    async def validate(self, credential, signal=None):
        return True

    async def refresh_token(self, credential, signal=None):
        result = self.refresh_results[self.refresh_calls]
        self.refresh_calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def _fresh_cred(access: str = "new-access") -> OAuthCredential:
    return OAuthCredential(
        access=access, refresh="r-new", expires=int(time.time() * 1000) + 3_600_000
    )


def _rate_limit_error(retry_after: str | None = None) -> Exception:
    e = RuntimeError("Request failed (429): rate_limit_error")
    e.status_code = 429  # type: ignore[attr-defined]
    if retry_after is not None:
        response = type("R", (), {"headers": {"retry-after": retry_after}})()
        e.response = response  # type: ignore[attr-defined]
    return e


def _oauth_manager(provider: _FakeOAuthProvider) -> AuthManager:
    registry = TextProviderRegistry()
    registry.register(provider)
    mgr = AuthManager.in_memory(ProviderRegistry(text=registry))
    mgr.set("fake-oauth", OAuthCredential(access="stale", refresh="r-old", expires=0))
    return mgr


@pytest.fixture
def sleep_spy(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("tau.auth.manager.asyncio.sleep", fake_sleep)
    return delays


class TestOAuthRefreshBackoff:
    @pytest.mark.asyncio
    async def test_transient_failure_retries_then_succeeds(self, sleep_spy):
        provider = _FakeOAuthProvider([_rate_limit_error(), _fresh_cred()])
        mgr = _oauth_manager(provider)

        api_key = await mgr.get_api_key("fake-oauth")

        assert api_key == "new-access"
        assert provider.refresh_calls == 2
        assert sleep_spy == [1.0]  # base delay, no Retry-After header
        assert mgr.last_refresh_error("fake-oauth") is None
        stored = mgr.get("fake-oauth")
        assert isinstance(stored, OAuthCredential) and stored.access == "new-access"

    @pytest.mark.asyncio
    async def test_retry_honors_retry_after_header(self, sleep_spy):
        provider = _FakeOAuthProvider([_rate_limit_error(retry_after="7"), _fresh_cred()])
        mgr = _oauth_manager(provider)

        api_key = await mgr.get_api_key("fake-oauth")

        assert api_key == "new-access"
        assert sleep_spy == [7.0]

    @pytest.mark.asyncio
    async def test_exhausted_retries_returns_none_and_keeps_credential(self, sleep_spy):
        errors = [_rate_limit_error(), _rate_limit_error(), _rate_limit_error()]
        provider = _FakeOAuthProvider(errors)
        mgr = _oauth_manager(provider)

        api_key = await mgr.get_api_key("fake-oauth")

        assert api_key is None
        assert provider.refresh_calls == 3  # 1 attempt + 2 retries
        assert mgr.has("fake-oauth") is True  # transient: credential kept
        assert mgr.last_refresh_error("fake-oauth") is errors[-1]

    @pytest.mark.asyncio
    async def test_unrecoverable_failure_no_retry_drops_credential(self, sleep_spy):
        provider = _FakeOAuthProvider([RuntimeError("Request failed (400): invalid_grant")])
        mgr = _oauth_manager(provider)

        api_key = await mgr.get_api_key("fake-oauth")

        assert api_key is None
        assert provider.refresh_calls == 1  # no retry on a dead refresh token
        assert sleep_spy == []
        assert mgr.has("fake-oauth") is False  # dropped so the caller prompts re-login
        assert mgr.last_refresh_error("fake-oauth") is not None

    @pytest.mark.asyncio
    async def test_force_refresh_retries_transient_failures(self, sleep_spy):
        provider = _FakeOAuthProvider([_rate_limit_error(), _fresh_cred()])
        mgr = _oauth_manager(provider)

        refreshed = await mgr.force_refresh("fake-oauth", stale_access="stale")

        assert refreshed is not None and refreshed.access == "new-access"
        assert provider.refresh_calls == 2
        assert sleep_spy == [1.0]

    @pytest.mark.asyncio
    async def test_successful_login_clears_refresh_error(self, sleep_spy):
        provider = _FakeOAuthProvider([_rate_limit_error(), _rate_limit_error(), _rate_limit_error()])
        mgr = _oauth_manager(provider)
        await mgr.get_api_key("fake-oauth")
        assert mgr.last_refresh_error("fake-oauth") is not None

        mgr.remove("fake-oauth")
        assert mgr.last_refresh_error("fake-oauth") is None
