"""Network request tracking and Fetch-domain interception."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .types import SessionID


@dataclass(slots=True)
class Request:
    request_id: str
    session_id: SessionID
    url: str
    method: str
    resource_type: str = ""
    frame_id: str | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    status: int | None = None
    failed: bool = False
    error: str | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


@dataclass(frozen=True, slots=True)
class InterceptDecision:
    action: Literal["continue", "block"] = "continue"
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None


RequestInterceptor = Callable[
    [Request], InterceptDecision | Awaitable[InterceptDecision]
]


class Network:
    """Track page traffic and optionally block or modify paused requests."""

    def __init__(self, browser: Any) -> None:
        self.browser = browser
        self.requests: dict[tuple[SessionID, str], Request] = {}
        self.blocked_url_patterns: tuple[str, ...] = ()
        self.interceptor: RequestInterceptor | None = None
        self._started = False
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._started:
            return
        client = self._client()
        client.register("Network.requestWillBeSent", self._on_request)
        client.register("Network.responseReceived", self._on_response)
        client.register("Network.loadingFinished", self._on_finished)
        client.register("Network.loadingFailed", self._on_failed)
        client.register("Fetch.requestPaused", self._on_paused)
        self._started = True
        for session_id in tuple(self.browser.session.session_to_target):
            await self.configure_session(session_id)

    async def stop(self) -> None:
        client = self.browser.client
        if client is not None and self._started:
            client.unregister("Network.requestWillBeSent", self._on_request)
            client.unregister("Network.responseReceived", self._on_response)
            client.unregister("Network.loadingFinished", self._on_finished)
            client.unregister("Network.loadingFailed", self._on_failed)
            client.unregister("Fetch.requestPaused", self._on_paused)
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def configure_session(self, session_id: SessionID) -> None:
        if not self._started:
            return
        client = self._client()
        if self.blocked_url_patterns or self.interceptor:
            await client.fetch.enable(
                {"patterns": [{"urlPattern": "*"}]},
                session_id=session_id,
            )

    async def set_interception(
        self,
        *,
        blocked_url_patterns: tuple[str, ...] = (),
        interceptor: RequestInterceptor | None = None,
    ) -> None:
        self.blocked_url_patterns = blocked_url_patterns
        self.interceptor = interceptor
        client = self._client()
        for session_id in tuple(self.browser.session.session_to_target):
            if blocked_url_patterns or interceptor:
                await client.fetch.enable(
                    {"patterns": [{"urlPattern": "*"}]},
                    session_id=session_id,
                )
            else:
                await client.fetch.disable(session_id=session_id)

    def for_session(self, session_id: SessionID) -> list[Request]:
        return [
            request
            for request in self.requests.values()
            if request.session_id == session_id
        ]

    def _on_request(self, params: dict[str, Any], session_id: SessionID | None) -> None:
        if session_id is None:
            return
        payload = params.get("request", {})
        self.requests[(session_id, params["requestId"])] = Request(
            request_id=params["requestId"],
            session_id=session_id,
            url=payload.get("url", ""),
            method=payload.get("method", "GET"),
            resource_type=params.get("type", ""),
            frame_id=params.get("frameId"),
            headers=payload.get("headers", {}),
        )

    def _on_response(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        request = self._request(session_id, params["requestId"])
        if request:
            request.status = params.get("response", {}).get("status")

    def _on_finished(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        request = self._request(session_id, params["requestId"])
        if request:
            request.finished.set()

    def _on_failed(self, params: dict[str, Any], session_id: SessionID | None) -> None:
        request = self._request(session_id, params["requestId"])
        if request:
            request.failed = True
            request.error = params.get("errorText", "request failed")
            request.finished.set()

    def _on_paused(self, params: dict[str, Any], session_id: SessionID | None) -> None:
        if session_id is None:
            return
        task = asyncio.create_task(self._handle_paused(params, session_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_paused(
        self, params: dict[str, Any], session_id: SessionID
    ) -> None:
        payload = params.get("request", {})
        request = Request(
            request_id=params.get("networkId", params["requestId"]),
            session_id=session_id,
            url=payload.get("url", ""),
            method=payload.get("method", "GET"),
            resource_type=params.get("resourceType", ""),
            frame_id=params.get("frameId"),
            headers=payload.get("headers", {}),
        )
        client = self._client()
        try:
            decision = InterceptDecision()
            if any(
                fnmatch.fnmatchcase(request.url, pattern)
                for pattern in self.blocked_url_patterns
            ):
                decision = InterceptDecision(action="block")
            elif self.interceptor:
                result = self.interceptor(request)
                decision = await result if inspect.isawaitable(result) else result
        except Exception as exc:
            request.failed = True
            request.error = f"interceptor failed: {exc}"
            request.finished.set()
            await client.fetch.fail_request(
                {"requestId": params["requestId"], "errorReason": "Aborted"},
                session_id=session_id,
            )
            return
        if decision.action == "block":
            await client.fetch.fail_request(
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
                session_id=session_id,
            )
            return
        overrides: dict[str, Any] = {"requestId": params["requestId"]}
        if decision.url is not None:
            overrides["url"] = decision.url
        if decision.method is not None:
            overrides["method"] = decision.method
        if decision.headers is not None:
            overrides["headers"] = [
                {"name": name, "value": value}
                for name, value in decision.headers.items()
            ]
        await client.fetch.continue_request(overrides, session_id=session_id)

    def _request(self, session_id: SessionID | None, request_id: str) -> Request | None:
        if session_id is None:
            return None
        return self.requests.get((session_id, request_id))

    def _client(self):
        if self.browser.client is None:
            raise RuntimeError("browser is not connected")
        return self.browser.client
