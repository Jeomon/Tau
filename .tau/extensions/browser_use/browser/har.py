"""HAR 1.2 recording via CDP Network events.

Only HTTPS traffic is recorded (a deliberate scope limit, matching common HAR
tooling defaults) since plaintext HTTP capture on a shared machine is a easy
way to accidentally persist credentials to disk.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import SessionID

_HAR_CREATOR = {"name": "browser", "version": "1.0"}


@dataclass(slots=True)
class _HarEntry:
    session_id: SessionID
    request: dict[str, Any]
    wall_time: float
    response: dict[str, Any] | None = None
    body: str | None = None
    body_base64: bool = False
    finished_wall_time: float | None = None
    failed: bool = False
    error_text: str = ""


class HarRecorder:
    """Capture HTTPS network traffic into a HAR 1.2 log file."""

    def __init__(self, browser: Any) -> None:
        self.browser = browser
        self._started = False
        self._entries: dict[tuple[SessionID, str], _HarEntry] = {}
        self._order: list[tuple[SessionID, str]] = []
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._started or self.browser.settings.record_har_path is None:
            return
        client = self._client()
        client.register("Network.requestWillBeSent", self._on_request)
        client.register("Network.responseReceived", self._on_response)
        client.register("Network.loadingFinished", self._on_finished)
        client.register("Network.loadingFailed", self._on_failed)
        self._started = True

    async def stop(self) -> None:
        client = self.browser.client
        if client is not None and self._started:
            client.unregister("Network.requestWillBeSent", self._on_request)
            client.unregister("Network.responseReceived", self._on_response)
            client.unregister("Network.loadingFinished", self._on_finished)
            client.unregister("Network.loadingFailed", self._on_failed)
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._started:
            path = self.browser.settings.record_har_path
            if path is not None:
                await self.save(path)
        self._started = False
        self._entries.clear()
        self._order.clear()

    async def save(self, path: str | Path) -> None:
        destination = Path(path).expanduser().resolve()
        har = self._build_har()
        payload = json.dumps(har, indent=2, ensure_ascii=False)
        await asyncio.to_thread(_write_har, destination, payload)

    def _build_har(self) -> dict[str, Any]:
        entries = []
        for key in self._order:
            entry = self._entries.get(key)
            if entry is not None:
                entries.append(_to_har_entry(entry))
        return {
            "log": {
                "version": "1.2",
                "creator": _HAR_CREATOR,
                "entries": entries,
            }
        }

    def _on_request(self, params: dict[str, Any], session_id: SessionID | None) -> None:
        if session_id is None:
            return
        request = params.get("request", {})
        if not request.get("url", "").startswith("https://"):
            return
        key = (session_id, params["requestId"])
        self._entries[key] = _HarEntry(
            session_id=session_id,
            request=request,
            wall_time=params.get("wallTime", 0.0),
        )
        self._order.append(key)

    def _on_response(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        entry = self._entries.get((session_id, params["requestId"]))
        if entry is not None:
            entry.response = params.get("response", {})

    def _on_finished(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        key = (session_id, params["requestId"])
        entry = self._entries.get(key)
        if entry is None or entry.response is None:
            return
        entry.finished_wall_time = entry.wall_time + params.get("timestamp", 0.0)
        task = asyncio.create_task(
            self._fetch_body(key), name=f"har-body-{params['requestId']}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_failed(self, params: dict[str, Any], session_id: SessionID | None) -> None:
        if session_id is None:
            return
        entry = self._entries.get((session_id, params["requestId"]))
        if entry is not None:
            entry.failed = True
            entry.error_text = params.get("errorText", "request failed")

    async def _fetch_body(self, key: tuple[SessionID, str]) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return
        session_id, request_id = key
        client = self.browser.client
        if client is None:
            return
        try:
            result = await client.network.get_response_body(
                {"requestId": request_id}, session_id=session_id
            )
        except Exception:
            return
        entry.body = result.get("body", "")
        entry.body_base64 = result.get("base64Encoded", False)

    def _client(self):
        if self.browser.client is None:
            raise RuntimeError("browser is not connected")
        return self.browser.client


def _to_har_entry(entry: _HarEntry) -> dict[str, Any]:
    response = entry.response or {}
    request_headers = entry.request.get("headers", {})
    response_headers = response.get("headers", {})
    started_at = entry.wall_time
    time_ms = (
        max(0.0, (entry.finished_wall_time - started_at) * 1000)
        if entry.finished_wall_time is not None
        else 0.0
    )
    content: dict[str, Any] = {
        "size": len(entry.body) if entry.body is not None else 0,
        "mimeType": response.get("mimeType", ""),
    }
    if entry.body is not None:
        content["text"] = entry.body
        if entry.body_base64:
            content["encoding"] = "base64"

    return {
        "startedDateTime": _iso_time(started_at),
        "time": time_ms,
        "request": {
            "method": entry.request.get("method", "GET"),
            "url": entry.request.get("url", ""),
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _to_har_headers(request_headers),
            "queryString": [],
            "postData": _post_data(entry.request),
            "headersSize": -1,
            "bodySize": -1,
        },
        "response": {
            "status": response.get("status", 0),
            "statusText": response.get("statusText", ""),
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _to_har_headers(response_headers),
            "content": content,
            "redirectURL": response.get("headers", {}).get("Location", ""),
            "headersSize": -1,
            "bodySize": -1,
        },
        "cache": {},
        "timings": {"send": -1, "wait": -1, "receive": -1},
    }


def _post_data(request: dict[str, Any]) -> dict[str, Any] | None:
    post_data = request.get("postData")
    if not post_data:
        return None
    return {
        "mimeType": request.get("headers", {}).get("Content-Type", ""),
        "text": post_data,
        "params": [],
    }


def _to_har_headers(headers: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": name, "value": str(value)} for name, value in headers.items()]


def _iso_time(wall_time: float) -> str:
    from datetime import datetime, timezone

    if not wall_time:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(wall_time, tz=timezone.utc).isoformat()


def _write_har(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
