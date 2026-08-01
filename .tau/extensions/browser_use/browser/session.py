"""Event-driven target and CDP session ownership."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .hooks import TabClosedEvent, TabCreatedEvent
from .types import (
    BrowserLaunchError,
    LoadState,
    NavigationError,
    NavigationTimeoutError,
    SessionID,
    TargetID,
)

if TYPE_CHECKING:
    from .page import Page
    from .service import Browser

_log = logging.getLogger(__name__)

_MISSING_SESSION_ERROR_CODE = -32001


def _is_missing_session_error(exc: BaseException) -> bool:
    """True if `exc` is CDP's "Session with given id not found" error.

    Raised when a command targets a `sessionId` whose target already
    detached — routine when connected to a real, actively-used browser
    window rather than one this process launched and fully controls.
    """
    if not exc.args:
        return False
    payload = exc.args[0]
    return isinstance(payload, dict) and payload.get("code") == _MISSING_SESSION_ERROR_CODE


@dataclass(slots=True)
class Target:
    target_id: TargetID
    target_type: str
    url: str = "about:blank"
    title: str = ""


@dataclass(slots=True)
class NavigationState:
    url: str = ""
    status: int | None = None
    error: str | None = None
    pending_requests: set[str] = field(default_factory=set)
    committed: asyncio.Event = field(default_factory=asyncio.Event)
    domcontentloaded: asyncio.Event = field(default_factory=asyncio.Event)
    load: asyncio.Event = field(default_factory=asyncio.Event)
    networkidle: asyncio.Event = field(default_factory=asyncio.Event)
    idle_task: asyncio.Task[object] | None = None

    def event_for(self, state: LoadState) -> asyncio.Event:
        return {
            "commit": self.committed,
            "domcontentloaded": self.domcontentloaded,
            "load": self.load,
            "networkidle": self.networkidle,
        }[state]

    def fail(self, error: str) -> None:
        self.error = error
        self.committed.set()
        self.domcontentloaded.set()
        self.load.set()
        self.networkidle.set()


class Session:
    """Maintain target/session mappings for one browser connection."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.targets: dict[TargetID, Target] = {}
        self.target_to_session: dict[TargetID, SessionID] = {}
        self.session_to_target: dict[SessionID, TargetID] = {}
        self.frame_to_target: dict[str, TargetID] = {}
        self.main_frame_by_session: dict[SessionID, str] = {}
        self.active_target_id: TargetID | None = None
        self._pages: dict[TargetID, Page] = {}
        self._ready: dict[TargetID, asyncio.Event] = {}
        self._navigation: dict[SessionID, NavigationState] = {}
        self._navigation_started: dict[SessionID, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        client = self._client()
        client.register("Target.attachedToTarget", self._on_attached)
        client.register("Target.detachedFromTarget", self._on_detached)
        client.register("Target.targetInfoChanged", self._on_target_info_changed)
        client.register("Target.targetDestroyed", self._on_target_destroyed)
        client.register("Page.frameStartedNavigating", self._on_navigation_started)
        client.register("Page.frameNavigated", self._on_frame_navigated)
        client.register("Page.domContentEventFired", self._on_dom_content_loaded)
        client.register("Page.loadEventFired", self._on_load)
        client.register("DOM.documentUpdated", self._on_document_updated)
        client.register("Network.requestWillBeSent", self._on_request_started)
        client.register("Network.responseReceived", self._on_response_received)
        client.register("Network.loadingFinished", self._on_request_finished)
        client.register("Network.loadingFailed", self._on_request_failed)

        await client.target.set_discover_targets({"discover": True})
        await client.target.set_auto_attach(
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            }
        )
        result = await client.target.get_targets()
        for info in result["targetInfos"]:
            await self._upsert_target(info)
            if info["type"] in {"page", "tab"}:
                await self.session_for(info["targetId"])

        if self.active_target_id is None:
            pages = self.pages()
            if pages:
                self.active_target_id = pages[-1].target_id
        self._started = True

    async def stop(self) -> None:
        client = self.browser.client
        if client is not None:
            client.unregister("Target.attachedToTarget", self._on_attached)
            client.unregister("Target.detachedFromTarget", self._on_detached)
            client.unregister("Target.targetInfoChanged", self._on_target_info_changed)
            client.unregister("Target.targetDestroyed", self._on_target_destroyed)
            client.unregister(
                "Page.frameStartedNavigating", self._on_navigation_started
            )
            client.unregister("Page.frameNavigated", self._on_frame_navigated)
            client.unregister("Page.domContentEventFired", self._on_dom_content_loaded)
            client.unregister("Page.loadEventFired", self._on_load)
            client.unregister("DOM.documentUpdated", self._on_document_updated)
            client.unregister("Network.requestWillBeSent", self._on_request_started)
            client.unregister("Network.responseReceived", self._on_response_received)
            client.unregister("Network.loadingFinished", self._on_request_finished)
            client.unregister("Network.loadingFailed", self._on_request_failed)
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        async with self._lock:
            self.targets.clear()
            self.target_to_session.clear()
            self.session_to_target.clear()
            self.frame_to_target.clear()
            self.main_frame_by_session.clear()
            self._pages.clear()
            self._ready.clear()
            for state in self._navigation.values():
                if state.idle_task:
                    state.idle_task.cancel()
            self._navigation.clear()
            self._navigation_started.clear()
            self.active_target_id = None
        self._started = False

    async def session_for(
        self,
        target_id: TargetID,
        *,
        focus: bool = False,
    ) -> SessionID:
        session_id = self.target_to_session.get(target_id)
        if session_id is None:
            if target_id not in self.targets:
                result = await self._client().target.get_target_info(
                    {"targetId": target_id}
                )
                await self._upsert_target(result["targetInfo"])
            result = await self._client().target.attach_to_target(
                {"targetId": target_id, "flatten": True}
            )
            session_id = result["sessionId"]
            target = self.targets[target_id]
            await self._store_session(target, session_id)

        if focus:
            await self.activate(target_id)
        return session_id

    async def wait_for_session(
        self, target_id: TargetID, timeout: float = 2.0
    ) -> SessionID:
        existing = self.target_to_session.get(target_id)
        if existing is not None:
            return existing
        event = self._ready.setdefault(target_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise BrowserLaunchError(
                f"target {target_id} did not attach within {timeout:g} seconds"
            ) from exc
        session_id = self.target_to_session.get(target_id)
        if session_id is None:
            raise BrowserLaunchError(f"target {target_id} detached before use")
        return session_id

    async def activate(self, target_id: TargetID) -> None:
        target = self.targets.get(target_id)
        if target is None or target.target_type not in {"page", "tab"}:
            raise BrowserLaunchError(f"target {target_id} is not an active page")
        await self._client().target.activate_target({"targetId": target_id})
        self.active_target_id = target_id

    async def page_for(self, target_id: TargetID, *, focus: bool = False) -> Page:
        target = self.targets.get(target_id)
        if target is None:
            result = await self._client().target.get_target_info(
                {"targetId": target_id}
            )
            target = await self._upsert_target(result["targetInfo"])
        if target.target_type not in {"page", "tab"}:
            raise BrowserLaunchError(f"target {target_id} is not a page")
        await self.session_for(target_id, focus=focus)
        page = self._pages.get(target_id)
        if page is None:
            from .page import Page

            page = Page(self.browser, target_id)
            self._pages[target_id] = page
        return page

    def pages(self) -> list[Page]:
        from .page import Page

        result: list[Page] = []
        for target in self.targets.values():
            if target.target_type not in {"page", "tab"}:
                continue
            page = self._pages.get(target.target_id)
            if page is None:
                page = Page(self.browser, target.target_id)
                self._pages[target.target_id] = page
            result.append(page)
        return result

    def current_page(self) -> Page | None:
        if self.active_target_id is None:
            return None
        return next(
            (page for page in self.pages() if page.target_id == self.active_target_id),
            None,
        )

    def target_for_session(self, session_id: SessionID) -> TargetID | None:
        return self.session_to_target.get(session_id)

    def target_for_frame(self, frame_id: str) -> TargetID | None:
        return self.frame_to_target.get(frame_id)

    def begin_navigation(self, session_id: SessionID, url: str) -> NavigationState:
        previous = self._navigation.get(session_id)
        if previous and previous.idle_task:
            previous.idle_task.cancel()
        state = NavigationState(url=url)
        self._navigation[session_id] = state
        started = self._navigation_started.pop(session_id, None)
        if started:
            started.set()
        return state

    def fail_navigation(self, session_id: SessionID, error: str) -> None:
        state = self._navigation.setdefault(session_id, NavigationState())
        state.fail(error)

    async def wait_for_load_state(
        self,
        session_id: SessionID,
        state: LoadState = "load",
        timeout: float = 30.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        navigation = self._navigation.get(session_id)
        if navigation is None:
            raise NavigationError("no navigation is being tracked for this page")
        try:
            await asyncio.wait_for(navigation.event_for(state).wait(), timeout)
        except TimeoutError as exc:
            raise NavigationTimeoutError(
                f"navigation to {navigation.url or '<unknown>'} did not reach "
                f"{state!r} within {timeout:g} seconds"
            ) from exc
        if navigation.error:
            raise NavigationError(navigation.error)

    async def wait_for_navigation(
        self,
        session_id: SessionID,
        *,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> str:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        navigation = self._navigation.get(session_id)
        if (
            navigation is None
            or navigation.error is not None
            or navigation.event_for(wait_until).is_set()
        ):
            started = self._navigation_started.setdefault(session_id, asyncio.Event())
            try:
                await asyncio.wait_for(started.wait(), timeout)
            except TimeoutError as exc:
                raise NavigationTimeoutError(
                    f"no navigation started within {timeout:g} seconds"
                ) from exc
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise NavigationTimeoutError(
                f"navigation did not reach {wait_until!r} within {timeout:g} seconds"
            )
        await self.wait_for_load_state(session_id, wait_until, remaining)
        return self._navigation[session_id].url

    def navigation_url(self, session_id: SessionID) -> str:
        state = self._navigation.get(session_id)
        return state.url if state else ""

    def navigation_status(self, session_id: SessionID) -> int | None:
        state = self._navigation.get(session_id)
        return state.status if state else None

    async def _upsert_target(self, info: dict[str, Any]) -> Target:
        target_id = info["targetId"]
        created = target_id not in self.targets
        target = self.targets.get(target_id)
        if target is None:
            target = Target(
                target_id=target_id,
                target_type=info.get("type", "unknown"),
                url=info.get("url", "about:blank"),
                title=info.get("title", ""),
            )
            self.targets[target_id] = target
        else:
            target.target_type = info.get("type", target.target_type)
            target.url = info.get("url", target.url)
            target.title = info.get("title", target.title)

        if created and target.target_type in {"page", "tab"}:
            await self.browser.hooks.emit(
                TabCreatedEvent(target_id=target_id, url=target.url)
            )
        return target

    async def _store_session(self, target: Target, session_id: SessionID) -> None:
        async with self._lock:
            old_session = self.target_to_session.get(target.target_id)
            if old_session and old_session != session_id:
                self.session_to_target.pop(old_session, None)
            self.target_to_session[target.target_id] = session_id
            self.session_to_target[session_id] = target.target_id
            self._ready.setdefault(target.target_id, asyncio.Event()).set()

        if target.target_type in {"page", "tab", "iframe"}:
            try:
                await asyncio.gather(
                    self._client().page.enable(session_id=session_id),
                    self._client().dom.enable(session_id=session_id),
                    self._client().runtime.enable(session_id=session_id),
                    self._client().network.enable(session_id=session_id),
                    self._client().accessibility.enable(session_id=session_id),
                    self._client().dom_snapshot.enable(session_id=session_id),
                    self._client().dom_storage.enable(session_id=session_id),
                    self._client().log.enable(session_id=session_id),
                    self._client().autofill.enable(session_id=session_id),
                )
            except Exception as exc:
                if _is_missing_session_error(exc):
                    # The target detached (closed/navigated away) between
                    # attaching and enabling its domains — nothing to set up
                    # for a session that is already gone. This is routine
                    # when connected to a real, actively-used Chrome window
                    # with short-lived targets (extension pages, preloads).
                    _log.debug(
                        "session %s detached before its domains were enabled",
                        session_id,
                    )
                    return
                raise
            await self.browser.network.configure_session(session_id)
            await self.browser.console.configure_session(session_id)
            await self._apply_fingerprint_overrides(session_id)

    async def _apply_fingerprint_overrides(self, session_id: SessionID) -> None:
        settings = self.browser.settings
        if not settings.stealth and not settings.user_agent:
            return
        client = self._client()
        if settings.stealth:
            from .stealth import STEALTH_SCRIPT

            await client.page.add_script_to_evaluate_on_new_document(
                {"source": STEALTH_SCRIPT, "runImmediately": True},
                session_id=session_id,
            )
            await client.emulation.set_automation_override(
                {"enabled": False}, session_id=session_id
            )
        if settings.user_agent:
            await client.emulation.set_user_agent_override(
                {"userAgent": settings.user_agent}, session_id=session_id
            )

    def _on_attached(
        self, params: dict[str, Any], _parent_session_id: SessionID | None
    ) -> None:
        self._create_task(self._handle_attached(params), "target-attached")

    async def _handle_attached(self, params: dict[str, Any]) -> None:
        target = await self._upsert_target(params["targetInfo"])
        await self._store_session(target, params["sessionId"])
        if params.get("waitingForDebugger"):
            await self._client().runtime.run_if_waiting_for_debugger(
                session_id=params["sessionId"]
            )

    def _on_detached(
        self, params: dict[str, Any], _parent_session_id: SessionID | None
    ) -> None:
        self._create_task(self._handle_detached(params), "target-detached")

    async def _handle_detached(self, params: dict[str, Any]) -> None:
        session_id = params["sessionId"]
        target_id = params.get("targetId") or self.session_to_target.get(session_id)
        if target_id is None:
            return
        async with self._lock:
            self.session_to_target.pop(session_id, None)
            if self.target_to_session.get(target_id) == session_id:
                self.target_to_session.pop(target_id, None)
        if target_id not in self.target_to_session:
            await self._remove_target(target_id)

    def _on_target_info_changed(
        self, params: dict[str, Any], _session_id: SessionID | None
    ) -> None:
        self._create_task(
            self._upsert_target(params["targetInfo"]),
            "target-info-changed",
        )

    def _on_target_destroyed(
        self, params: dict[str, Any], _session_id: SessionID | None
    ) -> None:
        self._create_task(
            self._remove_target(params["targetId"]),
            "target-destroyed",
        )

    def _on_navigation_started(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        frame_id = params.get("frameId")
        main_frame_id = self.main_frame_by_session.get(session_id)
        if main_frame_id and frame_id and frame_id != main_frame_id:
            return
        state = self._navigation.get(session_id)
        url = params.get("url", "")
        if state is None or state.load.is_set() or state.error:
            state = self.begin_navigation(session_id, url)
        else:
            state.url = url or state.url

    def _on_frame_navigated(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        self.browser.dom.invalidate(session_id, reset_previous=True)
        frame = params.get("frame", {})
        if "parentId" in frame:
            return
        frame_id = frame.get("id")
        if frame_id:
            self.main_frame_by_session[session_id] = frame_id
        state = self._navigation.get(session_id)
        if state is None:
            state = self.begin_navigation(session_id, frame.get("url", ""))
        state.url = frame.get("url", state.url)
        state.committed.set()
        target_id = self.session_to_target.get(session_id)
        if target_id and frame_id:
            self.frame_to_target[frame_id] = target_id

    def _on_dom_content_loaded(
        self, _params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is not None:
            state = self._navigation.get(session_id)
            if state is None:
                state = self.begin_navigation(session_id, "")
            state.domcontentloaded.set()

    def _on_load(self, _params: dict[str, Any], session_id: SessionID | None) -> None:
        if session_id is None:
            return
        state = self._navigation.get(session_id)
        if state is None:
            state = self.begin_navigation(session_id, "")
        state.load.set()
        self._schedule_network_idle(session_id, state)

    def _on_document_updated(
        self, _params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        self.browser.dom.invalidate(session_id)

    def _on_request_started(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        state = self._navigation.get(session_id)
        if state is None:
            state = self.begin_navigation(session_id, params.get("documentURL", ""))
        frame_id = params.get("frameId")
        main_frame_id = self.main_frame_by_session.get(session_id)
        if params.get("redirectResponse") and (
            not main_frame_id or frame_id == main_frame_id
        ):
            state.url = params.get("request", {}).get("url", state.url)
        if params.get("type") in {"WebSocket", "EventSource", "Media"}:
            return
        state.pending_requests.add(params["requestId"])
        state.networkidle.clear()
        if state.idle_task:
            state.idle_task.cancel()
            state.idle_task = None

    def _on_request_finished(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        state = self._navigation.get(session_id)
        if state is None:
            return
        state.pending_requests.discard(params["requestId"])
        self._schedule_network_idle(session_id, state)

    def _on_response_received(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None or params.get("type") != "Document":
            return
        main_frame_id = self.main_frame_by_session.get(session_id)
        if main_frame_id and params.get("frameId") != main_frame_id:
            return
        state = self._navigation.get(session_id)
        if state is None:
            state = self.begin_navigation(session_id, "")
        response = params.get("response", {})
        state.status = response.get("status")
        state.url = response.get("url", state.url)

    def _on_request_failed(
        self, params: dict[str, Any], session_id: SessionID | None
    ) -> None:
        if session_id is None:
            return
        state = self._navigation.get(session_id)
        if state is None:
            state = self.begin_navigation(session_id, "")
        state.pending_requests.discard(params["requestId"])
        is_main_document = params.get("type") == "Document" and (
            not self.main_frame_by_session.get(session_id)
            or params.get("frameId") == self.main_frame_by_session.get(session_id)
        )
        if is_main_document and not params.get("canceled", False):
            state.fail(params.get("errorText", "document request failed"))
        else:
            self._schedule_network_idle(session_id, state)

    def _schedule_network_idle(
        self, session_id: SessionID, state: NavigationState
    ) -> None:
        if state.pending_requests or not state.load.is_set():
            return
        if state.idle_task and not state.idle_task.done():
            return
        state.idle_task = asyncio.create_task(
            self._mark_network_idle(session_id, state),
            name="navigation-network-idle",
        )

    async def _mark_network_idle(
        self, session_id: SessionID, state: NavigationState
    ) -> None:
        try:
            await asyncio.sleep(0.5)
            if self._navigation.get(session_id) is state and not state.pending_requests:
                state.networkidle.set()
        except asyncio.CancelledError:
            raise

    async def _remove_target(self, target_id: TargetID) -> None:
        target = self.targets.pop(target_id, None)
        session_id = self.target_to_session.pop(target_id, None)
        if session_id:
            self.browser.dom.invalidate(session_id, reset_previous=True)
            self.session_to_target.pop(session_id, None)
            self.main_frame_by_session.pop(session_id, None)
            navigation = self._navigation.pop(session_id, None)
            if navigation:
                navigation.fail(f"target {target_id} closed during navigation")
                if navigation.idle_task:
                    navigation.idle_task.cancel()
            started = self._navigation_started.pop(session_id, None)
            if started:
                started.set()
        self._pages.pop(target_id, None)
        self.frame_to_target = {
            frame_id: mapped_target
            for frame_id, mapped_target in self.frame_to_target.items()
            if mapped_target != target_id
        }
        self._ready.pop(target_id, None)
        if self.active_target_id == target_id:
            remaining = self.pages()
            self.active_target_id = remaining[-1].target_id if remaining else None
        if target is not None and target.target_type in {"page", "tab"}:
            await self.browser.hooks.emit(TabClosedEvent(target_id=target_id))

    def _create_task(self, coroutine: object, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _log.error("session event handler failed", exc_info=task.exception())

    def _client(self):
        if self.browser.client is None:
            raise BrowserLaunchError("browser is not connected")
        return self.browser.client
