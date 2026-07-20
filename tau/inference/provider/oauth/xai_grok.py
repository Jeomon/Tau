"""
xAI Grok CLI OAuth flow — PKCE + local callback server.

The access token is used as a Bearer token for calls to
cli-chat-proxy.grok.com, the same proxy the official Grok CLI/Grok Build
uses to give SuperGrok/X Premium+ subscribers quota-based access to Grok
models without a separate pay-per-token API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tau.inference.provider.oauth.pkce import generate_pkce
from tau.inference.provider.oauth.types import (
    AbortSignal,
    OAuthAuthInfo,
    OAuthCredential,
    OAuthLoginCallbacks,
    OAuthPrompt,
)
from tau.inference.provider.oauth.utils import (
    await_oauth_code,
    get_oauth_ssl_context,
    is_headless_environment,
    parse_authorization_input,
    start_oauth_callback_server,
)
from tau.inference.provider.types import OAuthProvider

__all__ = ["XAIGrokOAuthProvider"]

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
ISSUER = "https://auth.x.ai"
AUTHORIZE_URL = f"{ISSUER}/oauth2/authorize"
TOKEN_URL = f"{ISSUER}/oauth2/token"
REVOKE_URL = f"{ISSUER}/oauth2/revoke"
USERINFO_URL = f"{ISSUER}/oauth2/userinfo"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 56121
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPES = "openid profile email offline_access grok-cli:access api:access"
# RFC 8628 device-code grant type used when polling the token endpoint.
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
# Fallback poll cadence when the server omits/zeroes `interval` (RFC 8628 allows 0).
_DEVICE_DEFAULT_INTERVAL_SECONDS = 5


def _build_authorization_url(challenge: str, state: str, nonce: str) -> str:
    """Build the xAI authorization URL with PKCE and state parameters."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "plan": "generic",
        "referrer": "tau",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, body: dict) -> dict:
    """POST a form-encoded request and return the parsed JSON response;
    raise RuntimeError on HTTP errors.
    """
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=get_oauth_ssl_context(), timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Request failed ({e.code}): {body_text}") from e


def _exchange_code(code: str, verifier: str) -> dict:
    """Exchange an authorization code for tokens using PKCE verification."""
    return _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )


def _refresh_token_sync(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token via xAI's token endpoint."""
    return _post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
    )


def _validate_token_sync(access_token: str) -> bool:
    """Check if the access token is valid by probing the userinfo endpoint."""
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, context=get_oauth_ssl_context(), timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:
        return False


def _revoke_token_sync(token: str) -> None:
    """Revoke a token at xAI's revocation endpoint (best-effort, silently ignores errors)."""
    with contextlib.suppress(Exception):
        _post_form(REVOKE_URL, {"token": token, "client_id": CLIENT_ID})


def _parse_token_response(data: dict) -> tuple[str, str, int]:
    """Extract (access_token, refresh_token, expires_ms) from an xAI token response."""
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not access or not isinstance(expires_in, (int, float)):
        raise ValueError(f"Token response missing fields: {data}")
    if not refresh:
        refresh = ""
    expires_ms = int(time.time() * 1000) + int(expires_in) * 1000 - 5 * 60 * 1000
    return access, refresh, expires_ms


def _post_form_allow_error(url: str, body: dict) -> tuple[int, dict]:
    """POST a form-encoded request and return (status, parsed_json).

    Unlike :func:`_post_form`, HTTP errors are not raised: the device-code token
    endpoint reports pending/slow-down states as ordinary 4xx responses carrying
    an ``error`` field, so the poller needs both the status and the JSON body.
    """
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=get_oauth_ssl_context(), timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"_raw": raw}
        return e.code, parsed


def _validate_verification_uri(raw: str) -> str:
    """Return ``raw`` if it is a well-formed https URL, else raise.

    The verification URI is surfaced to the user (and may be opened in a
    browser), so a malicious/malformed token response must not smuggle in a
    non-https target.
    """
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError as e:
        raise ValueError("Untrusted verification URI in xAI device response") from e
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Untrusted verification URI in xAI device response")
    return raw


def _request_xai_device_code() -> dict:
    """Request a device/user code pair from xAI's device-authorization endpoint."""
    status, data = _post_form_allow_error(
        DEVICE_CODE_URL,
        {"client_id": CLIENT_ID, "scope": SCOPES, "referrer": "tau"},
    )
    if status != 200:
        raise RuntimeError(f"xAI device authorization failed ({status}): {data}")
    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri") or data.get("verification_uri_complete")
    if not isinstance(device_code, str) or not isinstance(user_code, str) or not verification_uri:
        raise ValueError(f"Invalid xAI device code response: {data}")
    _validate_verification_uri(str(verification_uri))
    return data


async def _poll_for_xai_grok_token(
    device_code: str,
    interval_seconds: int,
    expires_in: int,
    signal: AbortSignal | None = None,
) -> dict:
    """Poll xAI's token endpoint until the user approves the device code.

    Returns the raw token response. Handles the RFC 8628 pending states
    (``authorization_pending`` / ``slow_down``) and terminal errors
    (``access_denied`` / ``expired_token``).
    """
    deadline = time.time() + expires_in
    interval_ms = max(1000, interval_seconds * 1000)

    while time.time() < deadline:
        if signal is not None and signal.is_set():
            raise RuntimeError("xAI device code login aborted")
        remaining = deadline - time.time()
        await asyncio.sleep(min(interval_ms / 1000, remaining))

        status, data = await asyncio.to_thread(
            _post_form_allow_error,
            TOKEN_URL,
            {
                "grant_type": _DEVICE_GRANT_TYPE,
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
        )

        if status == 200 and data.get("access_token"):
            return data

        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            raw_interval = data.get("interval")
            interval_ms = (
                int(raw_interval) * 1000
                if isinstance(raw_interval, (int, float)) and raw_interval > 0
                else interval_ms + 5000
            )
            continue
        if error in ("access_denied", "authorization_denied"):
            raise RuntimeError("xAI device authorization was denied")
        if error == "expired_token":
            raise RuntimeError("xAI device code expired before authorization")
        raise RuntimeError(f"xAI device token polling failed ({status}): {data}")

    raise RuntimeError("xAI device code login timed out")


async def login_xai_grok_device_code(callbacks: OAuthLoginCallbacks) -> OAuthCredential:
    """Run the xAI Grok device-code login for headless/remote environments.

    No local callback server or browser redirect is needed: the operator opens
    the verification URI on any device and enters the displayed user code.
    """
    device = await asyncio.to_thread(_request_xai_device_code)
    device_code = device["device_code"]
    user_code = device["user_code"]
    verification_uri = device.get("verification_uri") or device.get("verification_uri_complete")
    raw_interval = device.get("interval")
    interval = (
        int(raw_interval)
        if isinstance(raw_interval, (int, float)) and raw_interval > 0
        else _DEVICE_DEFAULT_INTERVAL_SECONDS
    )
    raw_expires = device.get("expires_in")
    expires_in = (
        int(raw_expires) if isinstance(raw_expires, (int, float)) and raw_expires > 0 else 900
    )

    callbacks.on_auth(
        OAuthAuthInfo(
            url=str(verification_uri),
            instructions=(
                f"Open {verification_uri} and enter code: {user_code} "
                "(requires a SuperGrok / X Premium+ subscription)."
            ),
        )
    )
    if callbacks.on_progress:
        callbacks.on_progress("Waiting for device authorization...")

    data = await _poll_for_xai_grok_token(device_code, interval, expires_in, callbacks.signal)
    access, refresh, expires_ms = _parse_token_response(data)
    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)


_GROK_AUTH_FILE = Path.home() / ".grok" / "auth.json"
_GROK_AUTH_KEY = f"{ISSUER}::{CLIENT_ID}"


def read_grok_file_credential() -> OAuthCredential | None:
    """Read the Grok CLI credential from ~/.grok/auth.json, if available.

    The official Grok CLI stores tokens keyed by "<issuer>::<client_id>",
    with the access token under "key" (a JWT) and expiry as an ISO 8601
    timestamp under "expires_at" — distinct field names from every other
    OAuth credential file this codebase reads.
    """
    try:
        data = json.loads(_GROK_AUTH_FILE.read_text(encoding="utf-8"))
        entry = data.get(_GROK_AUTH_KEY)
        if not isinstance(entry, dict):
            return None
        access = entry.get("key", "")
        refresh = entry.get("refresh_token", "")
        expires_at = entry.get("expires_at", "")
        if not refresh:
            return None
        expires_ms = int(
            dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() * 1000
        )
        return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)
    except Exception:
        return None


async def login_xai_grok(callbacks: OAuthLoginCallbacks) -> OAuthCredential:
    """Run the full xAI Grok OAuth login flow and return a fresh OAuthCredential.

    If a credential exists at ~/.grok/auth.json (written by the official
    Grok CLI) it is returned directly without opening a browser. On
    headless/remote hosts — or when the loopback callback server can't bind —
    the device-code flow is used instead.
    """
    file_cred = read_grok_file_credential()
    if file_cred is not None:
        return file_cred

    if is_headless_environment():
        return await login_xai_grok_device_code(callbacks)

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)
    url = _build_authorization_url(challenge, state, nonce)

    try:
        server, code_future = await start_oauth_callback_server(
            CALLBACK_PATH, state, CALLBACK_HOST, CALLBACK_PORT
        )
    except OSError:
        # Loopback callback couldn't bind (sandboxed/headless/port in use):
        # fall back to the device-code flow, which needs no local server.
        return await login_xai_grok_device_code(callbacks)
    callbacks.on_auth(
        OAuthAuthInfo(
            url=url,
            instructions=(
                "Complete xAI login in your browser (requires a SuperGrok / "
                "X Premium+ subscription). If the browser is on another "
                "machine, paste the final redirect URL here."
            ),
        )
    )

    code, recv_state = await await_oauth_code(code_future, state, server, callbacks)

    if not code:
        raw = await callbacks.on_prompt(
            OAuthPrompt(
                message="Paste the authorization code or full redirect URL:",
                placeholder=REDIRECT_URI,
            )
        )
        parsed_code, parsed_state = parse_authorization_input(raw)
        if parsed_state and parsed_state != state:
            raise ValueError("OAuth state mismatch")
        code = parsed_code
        recv_state = parsed_state or state

    if not code:
        raise ValueError("Missing authorization code")
    if not recv_state:
        raise ValueError("Missing OAuth state")

    if callbacks.on_progress:
        callbacks.on_progress("Exchanging authorization code for tokens...")

    data = await asyncio.to_thread(_exchange_code, code, verifier)
    access, refresh, expires_ms = _parse_token_response(data)

    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)


async def refresh_xai_grok_token(
    credential: OAuthCredential, signal: AbortSignal | None = None
) -> OAuthCredential:
    """Exchange a refresh token for a new OAuthCredential; transparent to the streaming loop."""
    data = await asyncio.to_thread(_refresh_token_sync, credential.refresh)
    access, new_refresh, expires_ms = _parse_token_response(data)
    refresh = new_refresh or credential.refresh
    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)


@dataclass
class XAIGrokOAuthProvider(OAuthProvider):
    """OAuthProvider implementation for xAI Grok CLI (SuperGrok / X Premium+) accounts."""

    id: str = "xai-grok"
    name: str = "xAI Grok CLI (SuperGrok Subscription)"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        """Initiate the OAuth login flow through xAI's authorization server."""
        return await login_xai_grok(callbacks)

    async def refresh_token(
        self, credential: OAuthCredential, signal: AbortSignal | None = None
    ) -> OAuthCredential:
        """Obtain a new access token using the stored refresh token."""
        return await refresh_xai_grok_token(credential, signal=signal)

    async def logout(self, credential: OAuthCredential) -> None:
        """Revoke the refresh token at xAI's revocation endpoint (best-effort)."""
        await asyncio.to_thread(_revoke_token_sync, credential.refresh)

    @property
    def api(self) -> str:
        """Registry key for the API class; resolved (and the SDK imported) lazily."""
        return "xai"

    async def validate(
        self, credential: OAuthCredential, signal: AbortSignal | None = None
    ) -> bool:
        """Return True if the credential is unexpired and accepted by the API."""
        if self.is_expired(credential):
            return False
        if signal and signal.is_set():
            return False
        return await asyncio.to_thread(_validate_token_sync, credential.access)
