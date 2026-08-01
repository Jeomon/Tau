"""Playwright-compatible browser storage-state persistence."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlsplit

from .hooks import (
    LoadStorageStateEvent,
    SaveStorageStateEvent,
    StorageStateLoadedEvent,
    StorageStateSavedEvent,
)

if TYPE_CHECKING:
    from .service import Browser


class StorageValue(TypedDict):
    name: str
    value: str


class OriginStorage(TypedDict):
    origin: str
    localStorage: list[StorageValue]
    sessionStorage: list[StorageValue]


class StorageState(TypedDict):
    cookies: list[dict[str, Any]]
    origins: list[OriginStorage]


_COOKIE_EXPORT_FIELDS = {
    "name",
    "value",
    "domain",
    "path",
    "secure",
    "httpOnly",
    "sameSite",
    "expires",
}

_COOKIE_IMPORT_FIELDS = {
    *_COOKIE_EXPORT_FIELDS,
    "url",
    "priority",
    "sourceScheme",
    "sourcePort",
    "partitionKey",
}


class Storage:
    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._pending_session_storage: dict[str, list[StorageValue]] = {}

    async def export_state(self) -> StorageState:
        client = self._client()
        cookie_result = await client.storage.get_cookies()
        cookies = [_export_cookie(cookie) for cookie in cookie_result["cookies"]]
        origins: list[OriginStorage] = []
        for origin, session_id in self._origin_sessions().items():
            await client.dom_storage.enable(session_id=session_id)
            local = await self._get_items(origin, session_id, local=True)
            session = await self._get_items(origin, session_id, local=False)
            origins.append(
                {
                    "origin": origin,
                    "localStorage": local,
                    "sessionStorage": session,
                }
            )
        return {"cookies": cookies, "origins": origins}

    async def import_state(self, state: StorageState) -> None:
        client = self._client()
        cookies = [_import_cookie(cookie) for cookie in state.get("cookies", [])]
        if cookies:
            await client.storage.set_cookies({"cookies": cookies})

        fallback_session = next(
            iter(self.browser.session.session_to_target),
            None,
        )
        origin_sessions = self._origin_sessions()
        for origin_state in state.get("origins", []):
            origin = _normalize_origin(origin_state["origin"])
            await self.browser.security.ensure_url_allowed(origin)
            session_id = origin_sessions.get(origin) or fallback_session
            if session_id is not None:
                await client.dom_storage.enable(session_id=session_id)
                await self._replace_items(
                    origin,
                    session_id,
                    origin_state.get("localStorage", []),
                    local=True,
                )
            session_items = origin_state.get("sessionStorage", [])
            matching_session = origin_sessions.get(origin)
            if matching_session is not None:
                await self._replace_items(
                    origin,
                    matching_session,
                    session_items,
                    local=False,
                )
            elif session_items:
                self._pending_session_storage[origin] = list(session_items)

    async def save(self, path: str | Path) -> StorageState:
        output = Path(path).expanduser().resolve()
        await self.browser.hooks.emit(SaveStorageStateEvent(path=str(output)))
        state = await self.export_state()
        payload = json.dumps(state, indent=2, ensure_ascii=False)
        await asyncio.to_thread(_write_private_json, output, payload)
        await self.browser.hooks.emit(
            StorageStateSavedEvent(
                path=str(output),
                cookies_count=len(state["cookies"]),
                origins_count=len(state["origins"]),
            )
        )
        return state

    async def load(self, path: str | Path) -> StorageState:
        source = Path(path).expanduser().resolve()
        await self.browser.hooks.emit(LoadStorageStateEvent(path=str(source)))
        payload = await asyncio.to_thread(source.read_text, encoding="utf-8")
        state = json.loads(payload)
        _validate_state(state)
        await self.import_state(state)
        await self.browser.hooks.emit(
            StorageStateLoadedEvent(
                path=str(source),
                cookies_count=len(state["cookies"]),
                origins_count=len(state["origins"]),
            )
        )
        return state

    async def apply_pending_session_storage(self, target_id: str, url: str) -> None:
        origin = _origin_from_url(url)
        items = self._pending_session_storage.get(origin)
        session_id = self.browser.session.target_to_session.get(target_id)
        if not items or session_id is None:
            return
        await self._replace_items(
            origin,
            session_id,
            items,
            local=False,
        )
        self._pending_session_storage.pop(origin, None)

    async def _get_items(
        self, origin: str, session_id: str, *, local: bool
    ) -> list[StorageValue]:
        result = await self._client().dom_storage.get_dom_storage_items(
            {
                "storageId": {
                    "securityOrigin": origin,
                    "isLocalStorage": local,
                }
            },
            session_id=session_id,
        )
        return [
            {"name": item[0], "value": item[1]}
            for item in result["entries"]
            if len(item) >= 2
        ]

    async def _replace_items(
        self,
        origin: str,
        session_id: str,
        items: list[StorageValue],
        *,
        local: bool,
    ) -> None:
        storage_id = {
            "securityOrigin": origin,
            "isLocalStorage": local,
        }
        client = self._client()
        await client.dom_storage.clear(
            {"storageId": storage_id},
            session_id=session_id,
        )
        for item in items:
            await client.dom_storage.set_dom_storage_item(
                {
                    "storageId": storage_id,
                    "key": item["name"],
                    "value": item["value"],
                },
                session_id=session_id,
            )

    def _origin_sessions(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for target_id, session_id in self.browser.session.target_to_session.items():
            target = self.browser.session.targets.get(target_id)
            if target is None:
                continue
            origin = _origin_from_url(target.url)
            if origin:
                result.setdefault(origin, session_id)
        return result

    def _client(self):
        if self.browser.client is None:
            raise RuntimeError("browser is not connected")
        return self.browser.client


def _export_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    exported = {
        key: value for key, value in cookie.items() if key in _COOKIE_EXPORT_FIELDS
    }
    exported.setdefault("sameSite", "Lax")
    return exported


def _import_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    imported = {
        key: value for key, value in cookie.items() if key in _COOKIE_IMPORT_FIELDS
    }
    if imported.get("expires") == -1:
        imported.pop("expires")
    return imported


def _origin_from_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _normalize_origin(origin: str) -> str:
    normalized = _origin_from_url(origin)
    if not normalized:
        raise ValueError(f"invalid storage origin: {origin!r}")
    return normalized


def _validate_state(state: object) -> None:
    if not isinstance(state, dict):
        raise ValueError("storage state must be a JSON object")
    if not isinstance(state.get("cookies", []), list):
        raise ValueError("storage state cookies must be a list")
    if not isinstance(state.get("origins", []), list):
        raise ValueError("storage state origins must be a list")


def _write_private_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        try:
            os.replace(path, backup)
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, path)
