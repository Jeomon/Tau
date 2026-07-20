"""Tests for the xAI Grok device-code login flow and headless auto-fallback.

Covers tau/inference/provider/oauth/xai_grok.py:
  - login_xai_grok_device_code() poll → token happy path (RFC 8628)
  - _validate_verification_uri() rejects non-https targets
  - login_xai_grok() auto-falls back to the device flow when headless / on bind failure
"""

from __future__ import annotations

import pytest

import tau.inference.provider.oauth.xai_grok as xai
from tau.inference.provider.oauth.types import OAuthLoginCallbacks


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Keep the poll loop's backoff from adding real wall-clock time to tests."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(xai.asyncio, "sleep", _instant)


def _make_callbacks() -> tuple[OAuthLoginCallbacks, list]:
    events: list = []
    return (
        OAuthLoginCallbacks(
            on_auth=lambda info: events.append(("auth", info)),
            on_prompt=lambda prompt: (_ for _ in ()).throw(AssertionError("prompt not expected")),
            on_progress=lambda msg: events.append(("progress", msg)),
        ),
        events,
    )


def test_validate_verification_uri_rejects_non_https():
    with pytest.raises(ValueError):
        xai._validate_verification_uri("http://auth.x.ai/device")
    with pytest.raises(ValueError):
        xai._validate_verification_uri("not a url")
    # A well-formed https URL passes through unchanged.
    assert xai._validate_verification_uri("https://auth.x.ai/device") == "https://auth.x.ai/device"


@pytest.mark.asyncio
async def test_device_flow_polls_then_returns_tokens(monkeypatch):
    """device/code → pending poll → slow_down → approved poll with tokens."""
    poll_calls = {"n": 0}

    def fake_post_form_allow_error(url, body):
        if url == xai.DEVICE_CODE_URL:
            assert body == {"client_id": xai.CLIENT_ID, "scope": xai.SCOPES, "referrer": "tau"}
            return 200, {
                "device_code": "dev-xyz",
                "user_code": "ABCD-1234",
                "verification_uri": "https://auth.x.ai/device",
                "interval": 0,
                "expires_in": 900,
            }
        if url == xai.TOKEN_URL:
            assert body == {
                "grant_type": xai._DEVICE_GRANT_TYPE,
                "client_id": xai.CLIENT_ID,
                "device_code": "dev-xyz",
            }
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                return 400, {"error": "authorization_pending"}
            if poll_calls["n"] == 2:
                return 400, {"error": "slow_down"}
            return 200, {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            }
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(xai, "_post_form_allow_error", fake_post_form_allow_error)

    callbacks, events = _make_callbacks()
    cred = await xai.login_xai_grok_device_code(callbacks)

    assert cred.access == "access-1"
    assert cred.refresh == "refresh-1"
    assert cred.expires > 0
    assert poll_calls["n"] == 3
    auth_events = [e for kind, e in events if kind == "auth"]
    assert auth_events and "ABCD-1234" in auth_events[0].instructions
    assert auth_events[0].url == "https://auth.x.ai/device"


@pytest.mark.asyncio
async def test_device_flow_raises_on_denied(monkeypatch):
    def fake_post_form_allow_error(url, body):
        if url == xai.DEVICE_CODE_URL:
            return 200, {
                "device_code": "dev-xyz",
                "user_code": "ABCD-1234",
                "verification_uri": "https://auth.x.ai/device",
                "interval": 0,
                "expires_in": 900,
            }
        return 400, {"error": "access_denied"}

    monkeypatch.setattr(xai, "_post_form_allow_error", fake_post_form_allow_error)
    callbacks, _ = _make_callbacks()
    with pytest.raises(RuntimeError, match="denied"):
        await xai.login_xai_grok_device_code(callbacks)


@pytest.mark.asyncio
async def test_login_auto_falls_back_to_device_when_headless(monkeypatch):
    monkeypatch.setattr(xai, "read_grok_file_credential", lambda: None)
    monkeypatch.setattr(xai, "is_headless_environment", lambda: True)

    called = {"device": False, "server": False}

    async def fake_device_login(callbacks):
        called["device"] = True
        return "sentinel-credential"

    async def fake_server(*args, **kwargs):
        called["server"] = True
        raise AssertionError("callback server must not start when headless")

    monkeypatch.setattr(xai, "login_xai_grok_device_code", fake_device_login)
    monkeypatch.setattr(xai, "start_oauth_callback_server", fake_server)

    callbacks, _ = _make_callbacks()
    result = await xai.login_xai_grok(callbacks)

    assert result == "sentinel-credential"
    assert called["device"] is True
    assert called["server"] is False


@pytest.mark.asyncio
async def test_login_falls_back_when_callback_server_cannot_bind(monkeypatch):
    monkeypatch.setattr(xai, "read_grok_file_credential", lambda: None)
    monkeypatch.setattr(xai, "is_headless_environment", lambda: False)

    async def fake_server(*args, **kwargs):
        raise OSError("address already in use")

    async def fake_device_login(callbacks):
        return "device-credential"

    monkeypatch.setattr(xai, "start_oauth_callback_server", fake_server)
    monkeypatch.setattr(xai, "login_xai_grok_device_code", fake_device_login)

    callbacks, _ = _make_callbacks()
    result = await xai.login_xai_grok(callbacks)

    assert result == "device-credential"
