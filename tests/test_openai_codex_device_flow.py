"""Tests for the Codex device-code login flow and headless auto-fallback.

Covers tau/inference/provider/oauth/openai_codex.py:
  - _is_headless() environment detection
  - login_openai_codex_device_code() poll → exchange happy path
  - login_openai_codex() auto-falls back to the device flow when headless
"""

from __future__ import annotations

import base64
import json

import pytest

import tau.inference.provider.oauth.openai_codex as codex
from tau.inference.provider.oauth.types import OAuthLoginCallbacks


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


def _access_token_with_account_id(account_id: str = "acct_123") -> str:
    """Build a minimal unsigned JWT carrying the chatgpt_account_id claim."""

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({codex.JWT_CLAIM_PATH: {"chatgpt_account_id": account_id}})
    return f"{header}.{payload}.sig"


class TestIsHeadless:
    def test_ssh_connection_is_headless(self, monkeypatch):
        monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
        assert codex._is_headless() is True

    def test_linux_without_display_is_headless(self, monkeypatch):
        monkeypatch.setattr(codex.sys, "platform", "linux")
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert codex._is_headless() is True

    def test_linux_with_display_is_not_headless(self, monkeypatch):
        monkeypatch.setattr(codex.sys, "platform", "linux")
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        assert codex._is_headless() is False

    def test_macos_is_not_headless(self, monkeypatch):
        monkeypatch.setattr(codex.sys, "platform", "darwin")
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        assert codex._is_headless() is False


@pytest.mark.asyncio
async def test_device_flow_polls_then_exchanges(monkeypatch):
    """usercode → pending poll → approved poll → token exchange via DEVICE_REDIRECT_URI."""
    access = _access_token_with_account_id("acct_abc")
    poll_calls = {"n": 0}
    exchange_args: dict = {}

    def fake_post_device_json(url, body):
        if url == codex.DEVICE_USER_CODE_URL:
            assert body == {"client_id": codex.CLIENT_ID}
            return 200, {"device_auth_id": "dev-1", "user_code": "WXYZ-1234", "interval": 0}
        if url == codex.DEVICE_TOKEN_URL:
            assert body == {"device_auth_id": "dev-1", "user_code": "WXYZ-1234"}
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                return 403, {}  # still pending
            return 200, {"authorization_code": "auth-code", "code_verifier": "server-verifier"}
        raise AssertionError(f"unexpected url {url}")

    def fake_post_token(payload):
        exchange_args.update(payload)
        return {"access_token": access, "refresh_token": "refresh-1", "expires_in": 3600}

    monkeypatch.setattr(codex, "_post_device_json", fake_post_device_json)
    monkeypatch.setattr(codex, "_post_token", fake_post_token)

    callbacks, events = _make_callbacks()
    cred = await codex.login_openai_codex_device_code(callbacks)

    assert cred.access == access
    assert cred.refresh == "refresh-1"
    assert cred.extra == {"account_id": "acct_abc"}
    # The device flow must exchange with the server-supplied verifier and the
    # device redirect URI, not the loopback callback.
    assert exchange_args["code"] == "auth-code"
    assert exchange_args["code_verifier"] == "server-verifier"
    assert exchange_args["redirect_uri"] == codex.DEVICE_REDIRECT_URI
    assert poll_calls["n"] == 2
    # The user code is surfaced through on_auth.
    auth_events = [e for kind, e in events if kind == "auth"]
    assert auth_events and "WXYZ-1234" in auth_events[0].instructions


@pytest.mark.asyncio
async def test_login_auto_falls_back_to_device_when_headless(monkeypatch):
    """login_openai_codex() routes to the device flow on a headless host."""
    monkeypatch.setattr(codex, "read_codex_file_credential", lambda: None)
    monkeypatch.setattr(codex, "_is_headless", lambda: True)

    called = {"device": False, "server": False}

    async def fake_device_login(callbacks):
        called["device"] = True
        return "sentinel-credential"

    async def fake_server(*args, **kwargs):
        called["server"] = True
        raise AssertionError("callback server must not start when headless")

    monkeypatch.setattr(codex, "login_openai_codex_device_code", fake_device_login)
    monkeypatch.setattr(codex, "start_oauth_callback_server", fake_server)

    callbacks, _ = _make_callbacks()
    result = await codex.login_openai_codex(callbacks)

    assert result == "sentinel-credential"
    assert called["device"] is True
    assert called["server"] is False


@pytest.mark.asyncio
async def test_login_falls_back_when_callback_server_cannot_bind(monkeypatch):
    """A bind failure (OSError) on the loopback server also triggers the device flow."""
    monkeypatch.setattr(codex, "read_codex_file_credential", lambda: None)
    monkeypatch.setattr(codex, "_is_headless", lambda: False)

    async def fake_server(*args, **kwargs):
        raise OSError("address already in use")

    async def fake_device_login(callbacks):
        return "device-credential"

    monkeypatch.setattr(codex, "start_oauth_callback_server", fake_server)
    monkeypatch.setattr(codex, "login_openai_codex_device_code", fake_device_login)

    callbacks, _ = _make_callbacks()
    result = await codex.login_openai_codex(callbacks)

    assert result == "device-credential"
