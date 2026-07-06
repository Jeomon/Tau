"""Bearer-token and OAuth (client_credentials + authorization_code) auth for
remote MCP servers. Credentials persist in a JSON file next to mcp.json."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx
from config import ServerConfig


class McpAuthError(Exception):
    pass


def _client_secret_field(oauth: dict[str, Any]) -> dict[str, str]:
    secret = oauth.get("client_secret")
    return {"client_secret": secret} if secret else {}


def _resolve_token(value: str | None) -> str | None:
    """Support literal values, `$ENV_VAR`, and `!shell-command` (matching
    Tau's own provider api_key convention in tau/extensions/api.py)."""
    if not value:
        return None
    if value.startswith("$") and not value.startswith("$env:"):
        return os.environ.get(value[1:])
    if value.startswith("$env:"):
        return os.environ.get(value[5:])
    if value.startswith("!"):
        import subprocess

        result = subprocess.run(value[1:], shell=True, capture_output=True, text=True, check=False)
        return result.stdout.strip()
    return value


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body>Authentication complete. You may close this tab.</body></html>"
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


class AuthManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        tmp.replace(self._path)
        with contextlib.suppress(OSError):
            self._path.chmod(0o600)

    def logout(self, server: str) -> None:
        self._data.pop(server, None)
        self._save()

    async def headers_for(self, server: ServerConfig) -> dict[str, str]:
        if server.auth == "bearer":
            token = _resolve_token(server.bearer_token)
            if not token:
                raise McpAuthError(
                    f"server {server.name!r}: auth=bearer but no bearerToken configured"
                )
            return {"Authorization": f"Bearer {token}"}

        if server.auth == "oauth":
            token = await self._get_valid_oauth_token(server)
            return {"Authorization": f"Bearer {token}"}

        return {}

    # ── OAuth ────────────────────────────────────────────────────────────

    async def _get_valid_oauth_token(self, server: ServerConfig) -> str:
        entry = self._data.get(server.name, {})
        if entry.get("access_token") and entry.get("expires_at", 0) > time.time() + 30:
            return entry["access_token"]

        grant_type = server.oauth.get("grant_type", "authorization_code")
        if grant_type == "client_credentials":
            return await self._client_credentials(server)

        if entry.get("refresh_token"):
            try:
                return await self._refresh(server, entry["refresh_token"])
            except McpAuthError:
                pass

        raise McpAuthError(
            f"server {server.name!r} needs interactive login — run `/mcp login {server.name}`"
        )

    async def login(self, server: ServerConfig) -> str:
        """Interactive authorization_code + PKCE flow via a local loopback callback."""
        oauth = server.oauth
        auth_endpoint = oauth.get("authorization_endpoint")
        token_endpoint = oauth.get("token_endpoint")
        client_id = oauth.get("client_id")
        if not (auth_endpoint and token_endpoint and client_id):
            raise McpAuthError(
                f"server {server.name!r}: oauth config needs authorization_endpoint, "
                "token_endpoint, and client_id"
            )

        port = oauth.get("redirect_port", 8765)
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if oauth.get("scope"):
            params["scope"] = oauth["scope"]

        url = f"{auth_endpoint}?{urllib.parse.urlencode(params)}"
        webbrowser.open(url)

        _CallbackHandler.result = {}
        httpd = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
        await asyncio.to_thread(httpd.handle_request)
        result = _CallbackHandler.result

        if result.get("state") != state:
            raise McpAuthError("oauth state mismatch — possible CSRF, aborting")
        code = result.get("code")
        if not code:
            raise McpAuthError(f"oauth callback missing code: {result}")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                    **_client_secret_field(oauth),
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        self._store_token(server.name, token_data)
        return token_data["access_token"]

    async def _client_credentials(self, server: ServerConfig) -> str:
        oauth = server.oauth
        token_endpoint = oauth.get("token_endpoint")
        client_id = oauth.get("client_id")
        client_secret = oauth.get("client_secret")
        if not (token_endpoint and client_id and client_secret):
            raise McpAuthError(
                f"server {server.name!r}: client_credentials oauth needs token_endpoint, "
                "client_id, and client_secret"
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    **({"scope": oauth["scope"]} if oauth.get("scope") else {}),
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        self._store_token(server.name, token_data)
        return token_data["access_token"]

    async def _refresh(self, server: ServerConfig, refresh_token: str) -> str:
        oauth = server.oauth
        token_endpoint = oauth.get("token_endpoint")
        client_id = oauth.get("client_id")
        if not (token_endpoint and client_id):
            raise McpAuthError(
                f"server {server.name!r}: missing token_endpoint/client_id for refresh"
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    **_client_secret_field(oauth),
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        self._store_token(server.name, token_data)
        return token_data["access_token"]

    def _store_token(self, server_name: str, token_data: dict[str, Any]) -> None:
        expires_in = token_data.get("expires_in", 3600)
        self._data[server_name] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": time.time() + float(expires_in),
        }
        self._save()
