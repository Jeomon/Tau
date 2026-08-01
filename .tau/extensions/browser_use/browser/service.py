"""Local Chromium process and remote CDP connection management."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import inspect
import json
import shutil
import sys
import tempfile
from asyncio.subprocess import Process
from pathlib import Path
from typing import Any, BinaryIO, Literal, Self
from urllib.parse import urljoin

from cdp import Client
from dom import DOM
from dom.types import Element

from hooks.service import Hooks

from .hooks import (
    BrowserStartEvent,
    BrowserStartedEvent,
    BrowserStartFailedEvent,
    BrowserErrorEvent,
    BrowserReconnectedEvent,
    BrowserReconnectingEvent,
    BrowserStopEvent,
    BrowserStoppedEvent,
    PageAttachedEvent,
    PageClosedEvent,
    PageCreatedEvent,
    BrowserStateRequestEvent,
    ClickElementEvent,
    ClickCoordinateEvent,
    DragEvent,
    GetDropdownOptionsEvent,
    GoBackEvent,
    GoForwardEvent,
    HoverEvent,
    KeyDownEvent,
    KeyUpEvent,
    NavigateToUrlEvent,
    NavigationStartedEvent,
    RefreshEvent,
    ScreenshotEvent,
    ScrollEvent,
    ScrollToTextEvent,
    SelectDropdownOptionEvent,
    SendKeysEvent,
    SwitchTabEvent,
    TypeTextEvent,
    UploadFileEvent,
    WaitEvent,
)
from .console import Console
from .download import Download, DownloadAction, DownloadRegistry
from .har import HarRecorder
from .network import Network, RequestInterceptor
from .page import Page
from .session import Session
from .settings import BrowserSettings
from .state import (
    DOMDiff,
    PageState,
    ViewportState,
)
from .storage import Storage, StorageState
from .types import (
    BrowserExecutableNotFound,
    BrowserLaunchError,
    BrowserState,
    BrowserStatus,
    LoadState,
    NavigationError,
    SessionID,
    StaleElementError,
    TargetID,
    WaitTimeoutError,
)

_EXECUTABLE_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "msedge",
)

_PLATFORM_PATHS = {
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ),
    "win32": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ),
}

_KEY_CODES = {
    "BACKSPACE": ("Backspace", "Backspace", 8),
    "DELETE": ("Delete", "Delete", 46),
    "ENTER": ("Enter", "Enter", 13),
    "ESC": ("Escape", "Escape", 27),
    "ESCAPE": ("Escape", "Escape", 27),
    "SPACE": (" ", "Space", 32),
    "TAB": ("Tab", "Tab", 9),
    "ARROWDOWN": ("ArrowDown", "ArrowDown", 40),
    "ARROWLEFT": ("ArrowLeft", "ArrowLeft", 37),
    "ARROWRIGHT": ("ArrowRight", "ArrowRight", 39),
    "ARROWUP": ("ArrowUp", "ArrowUp", 38),
}

_MODIFIERS = {"ALT": 1, "CTRL": 2, "CONTROL": 2, "META": 4, "CMD": 4, "SHIFT": 8}
_MODIFIER_KEYS = {
    "ALT": ("Alt", "AltLeft", 18),
    "CTRL": ("Control", "ControlLeft", 17),
    "CONTROL": ("Control", "ControlLeft", 17),
    "META": ("Meta", "MetaLeft", 91),
    "CMD": ("Meta", "MetaLeft", 91),
    "SHIFT": ("Shift", "ShiftLeft", 16),
}

_PROFILE_LOCK_MARKERS = (
    "singletonlock",
    "processsingleton",
    "already running",
    "already in use",
)


def _is_profile_lock_error(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _PROFILE_LOCK_MARKERS)


class Browser:
    """Manage a local Chromium process or connect to an existing remote browser."""

    def __init__(
        self,
        settings: BrowserSettings | None = None,
        hooks: Hooks | None = None,
    ) -> None:
        from watchdog import (
            DEFAULT_WATCHDOGS,
            SecurityWatchdog,
            WatchdogRegistry,
        )

        self.settings = settings or BrowserSettings()
        self.hooks = hooks or Hooks()
        self.session = Session(self)
        self.dom = DOM(self)
        self.storage = Storage(self)
        self.downloads = DownloadRegistry(self)
        self.network = Network(self)
        self.console = Console(self)
        self.har = HarRecorder(self)
        self.watchdog_registry = WatchdogRegistry(self)
        for watchdog_type in DEFAULT_WATCHDOGS:
            self.watchdog_registry.add(watchdog_type)
        security = self.watchdog_registry.get(SecurityWatchdog)
        if security is None:
            raise RuntimeError("default security watchdog was not registered")
        self.security = security
        self.state = BrowserState()
        self.client: Client | None = None
        self._process: Process | None = None
        self._temporary_profile: tempfile.TemporaryDirectory[str] | None = None
        self._stderr: BinaryIO | None = None
        self._lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._disconnect_callbacks: dict[Client, Any] = {}
        self._held_modifiers: dict[SessionID, set[str]] = {}

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.stop()

    @property
    def is_running(self) -> bool:
        if self.state.status is not BrowserStatus.RUNNING or self.client is None:
            return False
        return self.state.remote or (
            self._process is not None and self._process.returncode is None
        )

    async def start(self) -> Client:
        async with self._lock:
            if self.is_running and self.client is not None:
                return self.client
            if self.state.status is not BrowserStatus.STOPPED:
                raise BrowserLaunchError(
                    f"browser is currently {self.state.status.value}"
                )

            self.state = BrowserState(status=BrowserStatus.STARTING)
            await self.hooks.emit(BrowserStartEvent(settings=self.settings))
            try:
                await self.security.ensure_url_allowed(self.settings.initial_url)
                if self.settings.cdp_url is not None:
                    return await self._connect_remote(self.settings.cdp_url)

                if self.settings.user_data_dir is not None:
                    reuse_url = await self._find_live_profile_cdp_url()
                    if reuse_url is not None:
                        return await self._connect_remote(reuse_url)

                executable = self.find_executable(self.settings.executable_path)
                profile, cdp_url = await self._launch_process(executable)
                client = Client(cdp_url, timeout=self.settings.cdp_call_timeout)
                self._monitor_client(client)
                await client.__aenter__()
                self.client = client
                await self.session.start()
                await self.network.start()
                await self.console.start()
                await self.har.start()
                await self._configure_downloads(client)
                self.state = BrowserState(
                    status=BrowserStatus.RUNNING,
                    pid=self._process.pid,
                    cdp_url=cdp_url,
                    user_data_dir=profile,
                )
                await self.hooks.emit(BrowserStartedEvent(state=self.state))
                return client
            except BaseException as exc:
                await self._cleanup_failed_start()
                await self.hooks.emit(BrowserStartFailedEvent(error=exc))
                if isinstance(
                    exc,
                    (
                        BrowserExecutableNotFound,
                        BrowserLaunchError,
                        NavigationError,
                    ),
                ):
                    raise
                raise BrowserLaunchError(f"failed to start browser: {exc}") from exc

    async def stop(self) -> None:
        async with self._lock:
            if self.state.status is BrowserStatus.STOPPED:
                return
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reconnect_task
                self._reconnect_task = None
            previous_state = self.state
            await self.hooks.emit(BrowserStopEvent(state=previous_state))
            self.downloads.close()
            self.state = BrowserState(
                status=BrowserStatus.STOPPING,
                pid=self.state.pid,
                cdp_url=self.state.cdp_url,
                user_data_dir=self.state.user_data_dir,
                remote=self.state.remote,
            )

            owns_process = self._process is not None
            await self.network.stop()
            await self.console.stop()
            await self.har.stop()
            await self.session.stop()
            client, self.client = self.client, None
            if client is not None:
                self._unmonitor_client(client)
                if owns_process:
                    with contextlib.suppress(Exception):
                        await client.browser.close()
                with contextlib.suppress(Exception):
                    await client.__aexit__(None, None, None)

            await self._terminate_process()
            self._cleanup_profile()
            self.state = BrowserState()
            await self.hooks.emit(BrowserStoppedEvent(state=self.state))

    async def new_page(
        self,
        url: str = "about:blank",
        *,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> Page:
        client = self._require_client()
        result = await client.target.create_target({"url": "about:blank"})
        target_id = result["targetId"]
        page = await self.session.page_for(target_id, focus=True)
        await self.hooks.emit(PageCreatedEvent(target_id=target_id, url="about:blank"))
        if url != "about:blank":
            await page.navigate(url, wait_until=wait_until, timeout=timeout)
        return page

    async def close_page(self, page: Page | TargetID) -> bool:
        target_id = page.target_id if isinstance(page, Page) else page
        client = self._require_client()
        result = await client.target.close_target({"targetId": target_id})
        success = result["success"]
        await self.hooks.emit(PageClosedEvent(target_id=target_id, success=success))
        return success

    async def close_tab(self, page: Page | TargetID) -> bool:
        return await self.close_page(page)

    async def attach_page(self, target_id: TargetID) -> SessionID:
        session_id = await self.session.session_for(target_id)
        await self.hooks.emit(
            PageAttachedEvent(target_id=target_id, session_id=session_id)
        )
        return session_id

    async def pages(self) -> list[Page]:
        self._require_client()
        return self.session.pages()

    async def current_page(self) -> Page | None:
        self._require_client()
        return self.session.current_page()

    async def navigate(
        self,
        session_id: SessionID,
        url: str,
        *,
        new_tab: bool = False,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if new_tab:
            page = await self.new_page("about:blank")
            result = await page.navigate(
                url,
                wait_until=wait_until,
                timeout=timeout,
            )
            await self.hooks.emit(NavigateToUrlEvent(url=url, new_tab=True))
            return {"targetId": page.target_id, **result}

        client = self._require_client()
        target_id = self.session.target_for_session(session_id) or ""
        await self.security.ensure_url_allowed(url, target_id=target_id)
        self.dom.invalidate(session_id, reset_previous=True)
        self.session.begin_navigation(session_id, url)
        await self.hooks.emit(NavigationStartedEvent(target_id=target_id, url=url))
        result = await client.page.navigate({"url": url}, session_id=session_id)
        error = result.get("errorText")
        if error:
            self.session.fail_navigation(session_id, error)
            raise NavigationError(error)
        await self.session.wait_for_load_state(session_id, wait_until, timeout)
        final_url = self.session.navigation_url(session_id) or url
        status = self.session.navigation_status(session_id)
        try:
            await self.security.ensure_url_allowed(
                final_url,
                target_id=target_id,
            )
        except NavigationError:
            await client.page.navigate(
                {"url": "about:blank"},
                session_id=session_id,
            )
            raise
        await self.hooks.emit(
            NavigateToUrlEvent(
                url=url,
                wait_until=wait_until,
                timeout_ms=int(timeout * 1000),
            )
        )
        # NavigationCompleteEvent is no longer emitted here: NavigationWatchdog
        # now emits it for every main-frame navigation (Page.frameNavigated +
        # Page.frameStoppedLoading), not just ones that went through this
        # method. Emitting it here too would double-fire it for agent-
        # initiated navigation (SecurityWatchdog/StorageStateWatchdog would
        # process the same navigation twice) while still leaving click-
        # triggered, JS-redirected, back/forward, and refresh navigations
        # with no post-navigation event at all — exactly the gap that made
        # SecurityWatchdog's post-navigation check a no-op for anything but
        # explicit navigate() calls.
        if status is not None:
            result["status"] = status
        return result

    async def navigate_to_url(
        self,
        session_id: SessionID,
        url: str,
        *,
        new_tab: bool = False,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return await self.navigate(
            session_id,
            url,
            new_tab=new_tab,
            wait_until=wait_until,
            timeout=timeout,
        )

    async def click(
        self,
        session_id: SessionID,
        x: float,
        y: float,
        *,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
    ) -> None:
        if click_count < 1:
            raise ValueError("click_count must be at least 1")
        client = self._require_client()
        common = {
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
            "pointerType": "mouse",
        }
        await client.input.dispatch_mouse_event(
            {"type": "mousePressed", **common}, session_id=session_id
        )
        await client.input.dispatch_mouse_event(
            {"type": "mouseReleased", **common}, session_id=session_id
        )
        await self.hooks.emit(
            ClickCoordinateEvent(
                coordinate_x=int(x), coordinate_y=int(y), button=button
            )
        )
        self.dom.invalidate(session_id)

    async def click_element(
        self,
        session_id: SessionID,
        element: Element,
        *,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
        new_tab: bool = False,
    ) -> Page | None:
        if new_tab:
            # Chromium does not honor middle-click/Ctrl+click "open in new
            # tab" gestures for synthetic (CDP-injected) input — that
            # behavior is gated on trusted OS-level input, not just a
            # dispatched mousedown/mouseup with the right button/modifiers
            # (verified: neither reproduces it). A plain click only lands in
            # a new tab if the link itself carries target="_blank", which is
            # up to the page, not something we control. So the only
            # reliable way to force a link open in a new tab is to resolve
            # its href ourselves and open it the same way navigate(new_tab=
            # True) does, instead of trying to fake the click gesture.
            href = element.attributes.get("href") if element.tag_name == "a" else None
            if not href:
                raise NavigationError(
                    "click(new_tab=True) only works on <a href=...> elements: "
                    f"got <{element.tag_name}> with no href. Chromium doesn't "
                    "honor middle-click/Ctrl+click for synthetic input, so a "
                    "non-link element can't be reliably forced into a new tab."
                )
            target_id = self.session.target_for_session(session_id)
            base_url = self.session.targets[target_id].url if target_id else ""
            page = await self.new_page(urljoin(base_url, href))
            await self.hooks.emit(ClickElementEvent(node=element, button=button))
            return page

        x, y = await self._stable_element_center(session_id, element)
        await self._validate_element_hit(session_id, element, x, y)
        await self.click(
            session_id,
            x,
            y,
            button=button,
            click_count=click_count,
        )
        await self.hooks.emit(ClickElementEvent(node=element, button=button))
        return None

    async def hover(self, session_id: SessionID, x: float, y: float) -> None:
        client = self._require_client()
        await client.input.dispatch_mouse_event(
            {"type": "mouseMoved", "x": x, "y": y},
            session_id=session_id,
        )
        await self.hooks.emit(HoverEvent(coordinate_x=int(x), coordinate_y=int(y)))

    async def drag(
        self,
        session_id: SessionID,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        button: Literal["left", "right", "middle"] = "left",
        steps: int = 10,
    ) -> None:
        if steps < 1:
            raise ValueError("steps must be at least 1")
        start_x, start_y = start
        end_x, end_y = end
        buttons = {"left": 1, "right": 2, "middle": 4}[button]
        client = self._require_client()
        pressed = False
        try:
            await client.input.dispatch_mouse_event(
                {
                    "type": "mouseMoved",
                    "x": start_x,
                    "y": start_y,
                    "button": "none",
                    "buttons": 0,
                },
                session_id=session_id,
            )
            await client.input.dispatch_mouse_event(
                {
                    "type": "mousePressed",
                    "x": start_x,
                    "y": start_y,
                    "button": button,
                    "buttons": buttons,
                    "clickCount": 1,
                },
                session_id=session_id,
            )
            pressed = True
            for step in range(1, steps + 1):
                progress = step / steps
                await client.input.dispatch_mouse_event(
                    {
                        "type": "mouseMoved",
                        "x": start_x + (end_x - start_x) * progress,
                        "y": start_y + (end_y - start_y) * progress,
                        "button": button,
                        "buttons": buttons,
                    },
                    session_id=session_id,
                )
                if self.settings.drag_step_delay:
                    await asyncio.sleep(self.settings.drag_step_delay)
        finally:
            if pressed:
                await asyncio.shield(
                    client.input.dispatch_mouse_event(
                        {
                            "type": "mouseReleased",
                            "x": end_x,
                            "y": end_y,
                            "button": button,
                            "buttons": 0,
                            "clickCount": 1,
                        },
                        session_id=session_id,
                    )
                )
        await self.hooks.emit(
            DragEvent(
                start_x=int(start_x),
                start_y=int(start_y),
                end_x=int(end_x),
                end_y=int(end_y),
                button=button,
            )
        )

    async def type_text(
        self,
        session_id: SessionID,
        text: str,
        *,
        x: float | None = None,
        y: float | None = None,
        clear: bool = False,
        is_sensitive: bool = False,
        sensitive_key_name: str | None = None,
    ) -> None:
        if (x is None) != (y is None):
            raise ValueError("x and y must be provided together")
        if x is not None and y is not None:
            await self.click(session_id, x, y)
        if clear:
            await self._dispatch_key(
                session_id, "a", modifiers=2, commands=["selectAll"]
            )
            await self._dispatch_key(session_id, "Backspace")
        client = self._require_client()
        await client.input.insert_text({"text": text}, session_id=session_id)
        await self.hooks.emit(
            TypeTextEvent(
                text=text,
                clear=clear,
                is_sensitive=is_sensitive,
                sensitive_key_name=sensitive_key_name,
            )
        )
        self.dom.invalidate(session_id)

    async def send_keys(self, session_id: SessionID, keys: str) -> None:
        parts = [part.strip() for part in keys.split("+") if part.strip()]
        if not parts:
            raise ValueError("keys cannot be empty")
        modifiers = 0
        for modifier in parts[:-1]:
            try:
                modifiers |= _MODIFIERS[modifier.upper()]
            except KeyError as exc:
                raise ValueError(f"unsupported modifier: {modifier}") from exc
        await self._dispatch_key(session_id, parts[-1], modifiers=modifiers)
        await self.hooks.emit(SendKeysEvent(keys=keys))

    async def press(self, session_id: SessionID, key: str) -> None:
        await self.send_keys(session_id, key)

    async def key_down(self, session_id: SessionID, key: str) -> None:
        if not key:
            raise ValueError("key cannot be empty")
        normalized = key.upper()
        held = self._held_modifiers.setdefault(session_id, set())
        if normalized in _MODIFIERS:
            held.add(normalized)
        modifiers = self._modifier_mask(held)
        client = self._require_client()
        await client.input.dispatch_key_event(
            {"type": "rawKeyDown", **self._key_params(key), "modifiers": modifiers},
            session_id=session_id,
        )
        await self.hooks.emit(KeyDownEvent(key=key))

    async def key_up(self, session_id: SessionID, key: str) -> None:
        if not key:
            raise ValueError("key cannot be empty")
        normalized = key.upper()
        held = self._held_modifiers.setdefault(session_id, set())
        modifiers = self._modifier_mask(held)
        client = self._require_client()
        await client.input.dispatch_key_event(
            {"type": "keyUp", **self._key_params(key), "modifiers": modifiers},
            session_id=session_id,
        )
        held.discard(normalized)
        await self.hooks.emit(KeyUpEvent(key=key))

    async def type_into_element(
        self,
        session_id: SessionID,
        element: Element,
        text: str,
        *,
        clear: bool = False,
        is_sensitive: bool = False,
        sensitive_key_name: str | None = None,
    ) -> None:
        x, y = await self._stable_element_center(session_id, element)
        await self.type_text(
            session_id,
            text,
            x=x,
            y=y,
            clear=clear,
            is_sensitive=is_sensitive,
            sensitive_key_name=sensitive_key_name,
        )

    async def scroll_into_view(self, session_id: SessionID, element: Element) -> None:
        client = self._require_client()
        dom_session_id = element.session_id or session_id
        try:
            await client.dom.describe_node(
                {"backendNodeId": element.backend_node_id},
                session_id=dom_session_id,
            )
            await client.dom.scroll_into_view_if_needed(
                {"backendNodeId": element.backend_node_id},
                session_id=dom_session_id,
            )
        except Exception as exc:
            raise StaleElementError(
                f"element {element.backend_node_id} is detached"
            ) from exc

    async def wait_for_element(
        self,
        session_id: SessionID,
        predicate: Any = None,
        *,
        text: str | None = None,
        tag_name: str | None = None,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Element:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be greater than zero")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            state = await self.get_page_state(
                session_id,
                include_screenshot=False,
                include_accessibility=True,
            )
            for element in state.elements:
                matches = (text is None or text in element.text) and (
                    tag_name is None or element.tag_name == tag_name.lower()
                )
                if matches and predicate is not None:
                    result = predicate(element)
                    matches = bool(
                        await result if inspect.isawaitable(result) else result
                    )
                if matches:
                    return element
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"element was not found within {timeout:g} seconds"
                )
            await asyncio.sleep(min(poll_interval, deadline - loop.time()))

    async def wait_for_text(
        self,
        session_id: SessionID,
        text: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Element:
        if not text:
            raise ValueError("text cannot be empty")
        return await self.wait_for_element(
            session_id,
            text=text,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def wait_for_url(
        self,
        session_id: SessionID,
        pattern: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> str:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be greater than zero")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            url = str(await self.evaluate(session_id, "location.href"))
            if fnmatch.fnmatchcase(url, pattern):
                return url
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"URL did not match {pattern!r} within {timeout:g} seconds"
                )
            await asyncio.sleep(min(poll_interval, deadline - loop.time()))

    async def wait_for_function(
        self,
        session_id: SessionID,
        expression: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Any:
        if not expression:
            raise ValueError("expression cannot be empty")
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be greater than zero")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            result = await self.evaluate(session_id, expression)
            if result:
                return result
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"function remained false within {timeout:g} seconds"
                )
            await asyncio.sleep(min(poll_interval, deadline - loop.time()))

    async def set_request_interception(
        self,
        *,
        blocked_url_patterns: tuple[str, ...] = (),
        interceptor: RequestInterceptor | None = None,
    ) -> None:
        await self.network.set_interception(
            blocked_url_patterns=blocked_url_patterns,
            interceptor=interceptor,
        )

    async def scroll(
        self,
        session_id: SessionID,
        direction: Literal["up", "down", "left", "right"],
        amount: int,
        *,
        x: float = 0,
        y: float = 0,
    ) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        delta_x = (
            amount if direction == "right" else -amount if direction == "left" else 0
        )
        delta_y = amount if direction == "down" else -amount if direction == "up" else 0
        client = self._require_client()
        await client.input.dispatch_mouse_event(
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": delta_x,
                "deltaY": delta_y,
            },
            session_id=session_id,
        )
        await self.hooks.emit(ScrollEvent(direction=direction, amount=amount))
        self.dom.invalidate(session_id)

    async def go_back(self, session_id: SessionID) -> bool:
        moved = await self._navigate_history(session_id, -1)
        if moved:
            await self.hooks.emit(GoBackEvent())
        return moved

    async def go_forward(self, session_id: SessionID) -> bool:
        moved = await self._navigate_history(session_id, 1)
        if moved:
            await self.hooks.emit(GoForwardEvent())
        return moved

    async def refresh(
        self, session_id: SessionID, *, ignore_cache: bool = False
    ) -> None:
        client = self._require_client()
        self.dom.invalidate(session_id)
        await client.page.reload({"ignoreCache": ignore_cache}, session_id=session_id)
        await self.hooks.emit(RefreshEvent())

    async def screenshot(
        self,
        session_id: SessionID,
        *,
        full_page: bool = False,
        clip: dict[str, float] | None = None,
        format: Literal["png", "jpeg", "webp"] = "png",
        quality: int | None = None,
    ) -> str:
        if quality is not None and not 0 <= quality <= 100:
            raise ValueError("quality must be between 0 and 100")
        params: dict[str, Any] = {
            "format": format,
            "captureBeyondViewport": full_page,
        }
        if clip is not None:
            params["clip"] = {**clip, "scale": clip.get("scale", 1)}
        if quality is not None:
            params["quality"] = quality
        client = self._require_client()
        result = await client.send(
            "Page.captureScreenshot",
            params,
            session_id=session_id,
            timeout=self.settings.cdp_slow_call_timeout,
        )
        await self.hooks.emit(ScreenshotEvent(full_page=full_page, clip=clip))
        return result["data"]

    async def set_autofill_addresses(self, addresses: list[dict[str, Any]]) -> None:
        client = self._require_client()
        await client.autofill.set_addresses({"addresses": addresses})

    async def trigger_autofill(
        self,
        session_id: SessionID,
        element: Element,
        *,
        card: dict[str, Any] | None = None,
        address: dict[str, Any] | None = None,
    ) -> None:
        if (card is None) == (address is None):
            raise ValueError("exactly one of card or address must be provided")
        client = self._require_client()
        params: dict[str, Any] = {"fieldId": element.backend_node_id}
        if element.frame_id:
            params["frameId"] = element.frame_id
        if card is not None:
            params["card"] = card
        if address is not None:
            params["address"] = address
        await client.autofill.trigger(
            params, session_id=element.session_id or session_id
        )

    async def set_viewport(
        self,
        session_id: SessionID,
        width: int,
        height: int,
        *,
        device_scale_factor: float = 1.0,
        mobile: bool = False,
    ) -> None:
        client = self._require_client()
        await client.emulation.set_device_metrics_override(
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": device_scale_factor,
                "mobile": mobile,
            },
            session_id=session_id,
        )

    async def clear_viewport_override(self, session_id: SessionID) -> None:
        client = self._require_client()
        await client.emulation.clear_device_metrics_override(session_id=session_id)

    async def set_geolocation(
        self,
        session_id: SessionID,
        *,
        latitude: float,
        longitude: float,
        accuracy: float = 1.0,
    ) -> None:
        client = self._require_client()
        await client.emulation.set_geolocation_override(
            {"latitude": latitude, "longitude": longitude, "accuracy": accuracy},
            session_id=session_id,
        )

    async def clear_geolocation(self, session_id: SessionID) -> None:
        client = self._require_client()
        await client.emulation.clear_geolocation_override(session_id=session_id)

    async def upload_files(
        self,
        session_id: SessionID,
        x: float,
        y: float,
        files: list[str | Path],
    ) -> None:
        if not files:
            raise ValueError("at least one upload file is required")
        resolved = [str(Path(path).expanduser().resolve()) for path in files]
        missing = [path for path in resolved if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(f"upload files do not exist: {missing!r}")
        object_id = await self._element_object_id(session_id, x, y)
        client = self._require_client()
        await client.dom.set_file_input_files(
            {"files": resolved, "objectId": object_id}, session_id=session_id
        )
        await self.hooks.emit(
            UploadFileEvent(
                node={"x": x, "y": y}, file_path=resolved[0] if resolved else ""
            )
        )

    async def upload_file(
        self,
        session_id: SessionID,
        x: float,
        y: float,
        file_path: str | Path,
    ) -> None:
        await self.upload_files(session_id, x, y, [file_path])

    async def dropdown_options(
        self, session_id: SessionID, x: float, y: float
    ) -> list[dict[str, Any]]:
        expression = f"""
            (() => {{
                const el = document.elementFromPoint({json.dumps(x)}, {json.dumps(y)});
                if (!(el instanceof HTMLSelectElement)) throw new Error('coordinate is not a select element');
                return Array.from(el.options).map((o, index) => ({{
                    index, text: o.text, value: o.value, selected: o.selected, disabled: o.disabled
                }}));
            }})()
        """
        options = await self.evaluate(session_id, expression)
        await self.hooks.emit(GetDropdownOptionsEvent(node={"x": x, "y": y}))
        return options

    async def get_dropdown_options(
        self, session_id: SessionID, x: float, y: float
    ) -> list[dict[str, Any]]:
        return await self.dropdown_options(session_id, x, y)

    async def select_dropdown_option(
        self, session_id: SessionID, x: float, y: float, text: str
    ) -> bool:
        expression = f"""
            (() => {{
                const el = document.elementFromPoint({json.dumps(x)}, {json.dumps(y)});
                if (!(el instanceof HTMLSelectElement)) throw new Error('coordinate is not a select element');
                const option = Array.from(el.options).find(o => o.text === {json.dumps(text)});
                if (!option) return false;
                el.value = option.value;
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }})()
        """
        selected = bool(await self.evaluate(session_id, expression))
        await self.hooks.emit(
            SelectDropdownOptionEvent(node={"x": x, "y": y}, text=text)
        )
        return selected

    async def scroll_to_text(
        self,
        session_id: SessionID,
        text: str,
        *,
        direction: Literal["up", "down"] = "down",
    ) -> bool:
        expression = f"""
            (() => {{
                const needle = {json.dumps(text)}.toLowerCase();
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {{
                    if (node.textContent.toLowerCase().includes(needle)) {{
                        node.parentElement?.scrollIntoView({{
                            block: {json.dumps("start" if direction == "down" else "end")},
                            behavior: 'instant'
                        }});
                        return true;
                    }}
                }}
                return false;
            }})()
        """
        found = bool(await self.evaluate(session_id, expression))
        await self.hooks.emit(ScrollToTextEvent(text=text, direction=direction))
        return found

    async def switch_tab(self, target_id: TargetID) -> SessionID:
        await self.session.activate(target_id)
        session_id = await self.session.session_for(target_id)
        await self.hooks.emit(SwitchTabEvent(target_id=target_id))
        return session_id

    async def wait(self, seconds: float, *, max_seconds: float = 10.0) -> None:
        if seconds < 0 or max_seconds < 0:
            raise ValueError("wait durations must be non-negative")
        duration = min(seconds, max_seconds)
        await asyncio.sleep(duration)
        await self.hooks.emit(WaitEvent(seconds=duration, max_seconds=max_seconds))

    async def browser_state(self, session_id: SessionID) -> dict[str, Any]:
        client = self._require_client()
        metrics = await client.page.get_layout_metrics(session_id=session_id)
        await self.hooks.emit(
            BrowserStateRequestEvent(
                include_dom=False,
                include_screenshot=False,
                include_recent_events=False,
            )
        )
        return metrics

    async def get_page_state(
        self,
        session_id: SessionID,
        *,
        include_dom: bool = True,
        include_screenshot: bool = True,
        include_accessibility: bool = True,
        force_dom_refresh: bool = False,
    ) -> PageState:
        client = self._require_client()
        metrics = await client.page.get_layout_metrics(session_id=session_id)
        visual = metrics["cssVisualViewport"]
        page_info = await self.evaluate(
            session_id,
            "({url: location.href, title: document.title, "
            "devicePixelRatio: window.devicePixelRatio || 1})",
        )
        viewport = ViewportState(
            width=visual["clientWidth"],
            height=visual["clientHeight"],
            page_x=visual["pageX"],
            page_y=visual["pageY"],
            scale=visual["scale"],
            device_pixel_ratio=float(page_info.get("devicePixelRatio", 1)),
        )

        dom_state = (
            await self.dom.capture(
                session_id,
                viewport,
                include_dom=include_dom,
                include_accessibility=include_accessibility,
                force=force_dom_refresh,
            )
            if include_dom or include_accessibility
            else None
        )

        screenshot_data = (
            await self.screenshot(session_id) if include_screenshot else None
        )
        target_id = self.session.target_for_session(session_id) or ""
        await self.hooks.emit(
            BrowserStateRequestEvent(
                include_dom=include_dom,
                include_screenshot=include_screenshot,
            )
        )
        return PageState(
            target_id=target_id,
            url=str(page_info.get("url", "")),
            title=str(page_info.get("title", "")),
            viewport=viewport,
            elements=dom_state.elements if dom_state else (),
            accessibility=dom_state.accessibility if dom_state else (),
            screenshot=screenshot_data,
            dom_snapshot=dom_state.snapshot if dom_state else None,
            dom_diff=dom_state.diff if dom_state else DOMDiff(),
            dom_roots=dom_state.roots if dom_state else (),
            semantic_roots=dom_state.semantic_roots if dom_state else (),
            iframe_hints=dom_state.iframe_hints if dom_state else (),
            pagination_buttons=dom_state.pagination_buttons if dom_state else (),
        )

    async def export_storage_state(self) -> StorageState:
        return await self.storage.export_state()

    async def import_storage_state(self, state: StorageState) -> None:
        await self.storage.import_state(state)

    async def save_storage_state(self, path: str | Path | None = None) -> StorageState:
        destination = path or self.settings.storage_state_path
        if destination is None:
            raise ValueError(
                "storage state path is required; pass path or configure "
                "BrowserSettings.storage_state_path"
            )
        return await self.storage.save(destination)

    async def load_storage_state(self, path: str | Path | None = None) -> StorageState:
        source = path or self.settings.storage_state_path
        if source is None:
            raise ValueError(
                "storage state path is required; pass path or configure "
                "BrowserSettings.storage_state_path"
            )
        return await self.storage.load(source)

    async def expect_download(
        self,
        action: DownloadAction,
        *,
        target_id: TargetID | None = None,
        timeout: float = 30.0,
    ) -> Download:
        return await self.downloads.expect(
            target_id,
            action,
            timeout=timeout,
        )

    async def evaluate(self, session_id: SessionID, expression: str) -> Any:
        client = self._require_client()
        response = await client.runtime.evaluate(
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            session_id=session_id,
        )
        if "exceptionDetails" in response:
            details = response["exceptionDetails"]
            raise BrowserLaunchError(
                details.get("exception", {}).get("description")
                or details.get("text", "JavaScript evaluation failed")
            )
        result = response["result"]
        if result.get("subtype") == "error":
            raise BrowserLaunchError(
                result.get("description", "JavaScript evaluation failed")
            )
        return result.get("value")

    async def _dispatch_key(
        self,
        session_id: SessionID,
        key_name: str,
        *,
        modifiers: int = 0,
        commands: list[str] | None = None,
    ) -> None:
        params = self._key_params(key_name)
        params["modifiers"] = modifiers
        if commands:
            params["commands"] = commands
        client = self._require_client()
        await client.input.dispatch_key_event(
            {"type": "rawKeyDown", **params}, session_id=session_id
        )
        params.pop("commands", None)
        await client.input.dispatch_key_event(
            {"type": "keyUp", **params}, session_id=session_id
        )

    @staticmethod
    def _key_params(key_name: str) -> dict[str, Any]:
        normalized = key_name.upper()
        if normalized in _MODIFIER_KEYS:
            key, code, virtual_key = _MODIFIER_KEYS[normalized]
        elif normalized in _KEY_CODES:
            key, code, virtual_key = _KEY_CODES[normalized]
        elif len(key_name) == 1:
            key = key_name
            code = f"Key{key_name.upper()}" if key_name.isalpha() else ""
            virtual_key = ord(key_name.upper())
        else:
            key, code, virtual_key = key_name, key_name, 0

        return {
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": virtual_key,
            "nativeVirtualKeyCode": virtual_key,
        }

    @staticmethod
    def _modifier_mask(held: set[str]) -> int:
        return sum({_MODIFIERS[key] for key in held if key in _MODIFIERS})

    async def _stable_element_center(
        self, session_id: SessionID, element: Element
    ) -> tuple[float, float]:
        try:
            center = await self._element_center(session_id, element)
        except StaleElementError:
            element = await self._refresh_element(session_id, element)
            center = await self._element_center(session_id, element)
        for _ in range(self.settings.interaction_retries):
            if self.settings.interaction_retry_delay:
                await asyncio.sleep(self.settings.interaction_retry_delay)
            updated = await self._element_center(session_id, element)
            if abs(updated[0] - center[0]) < 0.5 and abs(updated[1] - center[1]) < 0.5:
                return updated
            center = updated
        return center

    async def _refresh_element(self, session_id: SessionID, stale: Element) -> Element:
        state = await self.get_page_state(
            session_id,
            include_screenshot=False,
            include_accessibility=True,
            force_dom_refresh=True,
        )
        if stale.element_id:
            exact = next(
                (
                    element
                    for element in state.elements
                    if element.element_id == stale.element_id
                ),
                None,
            )
            if exact is not None:
                return exact

        if stale.stable_hash:
            stable_candidates = [
                element
                for element in state.elements
                if element.stable_hash == stale.stable_hash
                and element.frame_id == stale.frame_id
                and (element.session_id or session_id)
                == (stale.session_id or session_id)
            ]
            if len(stable_candidates) == 1:
                return stable_candidates[0]

        identity = stale.semantic_key
        if not identity[2] and not identity[3]:
            raise StaleElementError(
                f"element {stale.backend_node_id} could not be refreshed"
            )
        candidates = [
            element
            for element in state.elements
            if element.semantic_key == identity
            and element.frame_id == stale.frame_id
            and (element.session_id or session_id) == (stale.session_id or session_id)
        ]
        if not candidates:
            raise StaleElementError(
                f"element {stale.backend_node_id} could not be refreshed"
            )
        return min(
            candidates,
            key=lambda element: (
                abs(element.bounds.x - stale.bounds.x)
                + abs(element.bounds.y - stale.bounds.y)
            ),
        )

    async def _element_center(
        self, session_id: SessionID, element: Element
    ) -> tuple[float, float]:
        await self.scroll_into_view(session_id, element)
        client = self._require_client()
        dom_session_id = element.session_id or session_id
        model = await client.dom.get_box_model(
            {"backendNodeId": element.backend_node_id},
            session_id=dom_session_id,
        )
        quad = model["model"].get("content") or model["model"].get("border")
        if not quad or len(quad) < 8:
            return element.bounds.center
        return (
            sum(quad[0::2]) / (len(quad) / 2) + element.frame_offset_x,
            sum(quad[1::2]) / (len(quad) / 2) + element.frame_offset_y,
        )

    async def _validate_element_hit(
        self,
        session_id: SessionID,
        element: Element,
        x: float,
        y: float,
    ) -> None:
        client = self._require_client()
        dom_session_id = element.session_id or session_id
        local_x = x - element.frame_offset_x
        local_y = y - element.frame_offset_y
        try:
            result = await client.dom.get_node_for_location(
                {
                    "x": int(local_x),
                    "y": int(local_y),
                    "includeUserAgentShadowDOM": True,
                    "ignorePointerEventsNone": False,
                },
                session_id=dom_session_id,
            )
        except Exception:
            return
        hit_id = result.get("backendNodeId")
        if not hit_id or hit_id == element.backend_node_id:
            return
        state = self.dom.latest(session_id)
        if state is None:
            return
        by_backend = {
            (candidate.session_id or session_id, candidate.backend_node_id): candidate
            for candidate in state.elements
        }
        hit = by_backend.get((dom_session_id, hit_id))
        if hit is None:
            return
        parent_id = hit.parent_backend_node_id
        while parent_id:
            if parent_id == element.backend_node_id:
                return
            parent = by_backend.get((dom_session_id, parent_id))
            parent_id = parent.parent_backend_node_id if parent else None
        raise StaleElementError(
            f"element {element.backend_node_id} is covered by element {hit_id}"
        )

    async def _navigate_history(self, session_id: SessionID, offset: int) -> bool:
        client = self._require_client()
        history = await client.page.get_navigation_history(session_id=session_id)
        target_index = history["currentIndex"] + offset
        if target_index < 0 or target_index >= len(history["entries"]):
            return False
        await client.page.navigate_to_history_entry(
            {"entryId": history["entries"][target_index]["id"]},
            session_id=session_id,
        )
        return True

    async def _element_object_id(
        self, session_id: SessionID, x: float, y: float
    ) -> str:
        client = self._require_client()
        response = await client.runtime.evaluate(
            {
                "expression": (
                    f"document.elementFromPoint({json.dumps(x)}, {json.dumps(y)})"
                ),
                "returnByValue": False,
                "userGesture": True,
            },
            session_id=session_id,
        )
        if "exceptionDetails" in response:
            raise BrowserLaunchError("failed to resolve element at upload coordinates")
        result = response["result"]
        object_id = result.get("objectId")
        if not object_id or result.get("subtype") == "null":
            raise BrowserLaunchError(f"no element found at coordinates ({x}, {y})")
        return object_id

    async def _connect_remote(self, cdp_url: str) -> Client:
        client = Client(cdp_url, timeout=self.settings.cdp_call_timeout)
        self._monitor_client(client)
        await client.__aenter__()
        self.client = client
        await self.session.start()
        await self.network.start()
        await self.console.start()
        await self.har.start()
        await self._configure_downloads(client)
        self.state = BrowserState(
            status=BrowserStatus.RUNNING,
            cdp_url=cdp_url,
            remote=True,
        )
        await self.hooks.emit(BrowserStartedEvent(state=self.state))
        return client

    def _monitor_client(self, client: Client) -> None:
        def on_disconnect(error: BaseException | None) -> None:
            self._schedule_disconnect(client, error)

        self._disconnect_callbacks[client] = on_disconnect
        client.on_disconnect(on_disconnect)

    def _unmonitor_client(self, client: Client) -> None:
        callback = self._disconnect_callbacks.pop(client, None)
        if callback is not None:
            client.remove_disconnect_handler(callback)

    def _schedule_disconnect(
        self,
        disconnected_client: Client,
        error: BaseException | None,
    ) -> None:
        if disconnected_client is not self.client or self.state.status in {
            BrowserStatus.STOPPED,
            BrowserStatus.STOPPING,
        }:
            return
        if self.state.remote and self.settings.reconnect_attempts > 0:
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_remote(disconnected_client, error),
                    name="browser-remote-reconnect",
                )
            return
        self._reconnect_task = asyncio.create_task(
            self._handle_terminal_disconnect(disconnected_client, error),
            name="browser-disconnected",
        )

    async def _handle_terminal_disconnect(
        self,
        disconnected_client: Client,
        error: BaseException | None,
    ) -> None:
        await self.network.stop()
        await self.console.stop()
        await self.har.stop()
        await self.session.stop()
        self._unmonitor_client(disconnected_client)
        with contextlib.suppress(Exception):
            await disconnected_client.close()
        if self.client is disconnected_client:
            self.client = None
        self.state = BrowserState(
            status=BrowserStatus.DISCONNECTED,
            pid=self.state.pid,
            cdp_url=self.state.cdp_url,
            user_data_dir=self.state.user_data_dir,
            remote=self.state.remote,
        )
        await self.hooks.emit(
            BrowserErrorEvent(
                error_type="CDPDisconnected",
                message=str(error or "CDP WebSocket disconnected"),
                details={"cdp_url": self.state.cdp_url},
            )
        )

    async def _reconnect_remote(
        self,
        disconnected_client: Client,
        disconnect_error: BaseException | None,
    ) -> None:
        cdp_url = self.settings.cdp_url
        if cdp_url is None or disconnected_client is not self.client:
            return
        loop = asyncio.get_running_loop()
        disconnected_at = loop.time()
        previous_target_id = self.session.active_target_id
        previous_url = ""
        if previous_target_id and previous_target_id in self.session.targets:
            previous_url = self.session.targets[previous_target_id].url

        self.state = BrowserState(
            status=BrowserStatus.RECONNECTING,
            cdp_url=cdp_url,
            remote=True,
        )
        await self.network.stop()
        await self.console.stop()
        await self.har.stop()
        await self.session.stop()
        self._unmonitor_client(disconnected_client)
        with contextlib.suppress(Exception):
            await disconnected_client.close()
        if self.client is disconnected_client:
            self.client = None

        last_error: BaseException | None = disconnect_error
        for attempt in range(1, self.settings.reconnect_attempts + 1):
            if self.state.status is BrowserStatus.STOPPING:
                return
            if attempt > 1 and self.settings.reconnect_delay:
                await asyncio.sleep(self.settings.reconnect_delay)
            await self.hooks.emit(
                BrowserReconnectingEvent(
                    cdp_url=cdp_url,
                    attempt=attempt,
                    max_attempts=self.settings.reconnect_attempts,
                )
            )
            client = Client(cdp_url, timeout=self.settings.cdp_call_timeout)
            self._monitor_client(client)
            try:
                await asyncio.wait_for(
                    client.connect(),
                    timeout=self.settings.reconnect_timeout,
                )
                self.client = client
                await self.session.start()
                await self.network.start()
                await self.console.start()
                await self.har.start()
                await self._configure_downloads(client)
                await self._recover_page_focus(previous_target_id, previous_url)
            except asyncio.CancelledError:
                self._unmonitor_client(client)
                with contextlib.suppress(Exception):
                    await client.close()
                raise
            except BaseException as exc:
                last_error = exc
                await self.network.stop()
                await self.console.stop()
                await self.har.stop()
                await self.session.stop()
                if self.client is client:
                    self.client = None
                self._unmonitor_client(client)
                with contextlib.suppress(Exception):
                    await client.close()
                continue

            self.state = BrowserState(
                status=BrowserStatus.RUNNING,
                cdp_url=cdp_url,
                remote=True,
            )
            await self.hooks.emit(
                BrowserReconnectedEvent(
                    cdp_url=cdp_url,
                    attempt=attempt,
                    downtime_seconds=loop.time() - disconnected_at,
                )
            )
            return

        self.state = BrowserState(
            status=BrowserStatus.DISCONNECTED,
            cdp_url=cdp_url,
            remote=True,
        )
        await self.hooks.emit(
            BrowserErrorEvent(
                error_type="CDPReconnectFailed",
                message=str(last_error or "remote CDP reconnection failed"),
                details={
                    "cdp_url": cdp_url,
                    "attempts": self.settings.reconnect_attempts,
                },
            )
        )

    async def _recover_page_focus(
        self,
        previous_target_id: TargetID | None,
        previous_url: str,
    ) -> None:
        if previous_target_id and previous_target_id in self.session.targets:
            await self.session.activate(previous_target_id)
            return
        if previous_url:
            for target in self.session.targets.values():
                if target.target_type in {"page", "tab"} and target.url == previous_url:
                    await self.session.activate(target.target_id)
                    return
        pages = self.session.pages()
        if pages:
            await self.session.activate(pages[-1].target_id)

    async def _configure_downloads(self, client: Client) -> None:
        if self.settings.downloads_path is None:
            return
        Path(self.settings.downloads_path).mkdir(parents=True, exist_ok=True)
        await client.browser.set_download_behavior(
            {
                "behavior": "allow",
                "downloadPath": str(Path(self.settings.downloads_path).resolve()),
                "eventsEnabled": True,
            }
        )

    @staticmethod
    def find_executable(configured_path: Path | str | None = None) -> Path:
        if configured_path is not None:
            path = Path(configured_path).expanduser()
            if path.is_file():
                return path
            raise BrowserExecutableNotFound(
                f"browser executable does not exist: {path}"
            )

        for name in _EXECUTABLE_NAMES:
            discovered = shutil.which(name)
            if discovered:
                return Path(discovered)
        for candidate in _PLATFORM_PATHS.get(sys.platform, ()):
            path = Path(candidate)
            if path.is_file():
                return path
        raise BrowserExecutableNotFound(
            "no Chromium browser found; set BrowserSettings(executable_path=...)"
        )

    async def _launch_process(self, executable: Path) -> tuple[Path, str]:
        """Launch Chromium, retrying once with a fresh temp profile on a profile-lock error.

        A configured `user_data_dir` may already be held by another running
        Chrome instance (e.g. the user's real browser, or a leftover process
        from a previous crashed run). Rather than failing outright, fall back
        to an ephemeral profile so the caller still gets a working browser.
        """
        profile = self._prepare_profile()
        retry_with_fallback_profile = self.settings.user_data_dir is not None
        for attempt in range(2):
            command = self._build_command(executable, profile)
            self._stderr = tempfile.TemporaryFile()
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=self._stderr,
                env=self.settings.process_env(),
            )
            try:
                cdp_url = await self._wait_for_cdp_url(profile)
                return profile, cdp_url
            except BrowserLaunchError as exc:
                if (
                    attempt == 0
                    and retry_with_fallback_profile
                    and _is_profile_lock_error(str(exc))
                ):
                    await self._terminate_process()
                    profile = self._prepare_fallback_profile()
                    continue
                raise
        raise BrowserLaunchError("browser did not start after profile-lock retry")

    def _prepare_profile(self) -> Path:
        if self.settings.user_data_dir is not None:
            profile = Path(self.settings.user_data_dir).resolve()
            profile.mkdir(parents=True, exist_ok=True)
            # A reused profile still holds the DevToolsActivePort file that the
            # previous run's Chrome wrote (it is not always cleaned up on exit).
            # _wait_for_cdp_url would read that stale port and connect to a dead
            # endpoint, so remove it before launch and let this run's Chrome
            # write a fresh one. (Ephemeral profiles start empty, so this only
            # matters for a configured user_data_dir.)
            with contextlib.suppress(OSError):
                (profile / "DevToolsActivePort").unlink(missing_ok=True)
            return profile
        self._temporary_profile = tempfile.TemporaryDirectory(prefix="local-browser-")
        return Path(self._temporary_profile.name)

    def _prepare_fallback_profile(self) -> Path:
        """Force a fresh temporary profile, bypassing a configured user_data_dir.

        Used when the configured profile directory turns out to be locked by
        another running Chrome instance.
        """
        self._temporary_profile = tempfile.TemporaryDirectory(
            prefix="local-browser-retry-"
        )
        return Path(self._temporary_profile.name)

    def _build_command(self, executable: Path, profile: Path) -> list[str]:
        command = [
            str(executable),
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
        ]
        if self.settings.headless:
            command.append("--headless=new")
        if not self.settings.chromium_sandbox:
            command.append("--no-sandbox")
        if self.settings.downloads_path is not None:
            Path(self.settings.downloads_path).mkdir(parents=True, exist_ok=True)
        command.extend(self.settings.args)
        command.append(self.settings.initial_url)
        return command

    async def _find_live_profile_cdp_url(self) -> str | None:
        """Detect a browser from a previous session still running against the
        configured `user_data_dir` and return its CDP URL so `start()` attaches
        to that instance instead of spawning a second Chrome pointed at the
        same profile (which just collides with the OS-level singleton lock and
        fails). Returns None if the profile isn't in use by a live process, so
        the normal fresh-launch path runs instead.
        """
        assert self.settings.user_data_dir is not None
        active_port_file = (
            Path(self.settings.user_data_dir).expanduser().resolve()
            / "DevToolsActivePort"
        )
        try:
            lines = active_port_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if len(lines) < 2:
            return None
        try:
            port = int(lines[0])
        except ValueError:
            return None
        ws_url = f"ws://127.0.0.1:{port}{lines[1]}"
        probe = Client(ws_url, timeout=2.0)
        try:
            await probe.__aenter__()
        except Exception:
            return None
        await probe.__aexit__(None, None, None)
        return ws_url

    async def _wait_for_cdp_url(self, profile: Path) -> str:
        active_port_file = profile / "DevToolsActivePort"
        deadline = asyncio.get_running_loop().time() + self.settings.startup_timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._process is None or self._process.returncode is not None:
                code = None if self._process is None else self._process.returncode
                detail = self._read_stderr()
                message = f"browser exited during startup with code {code}"
                if detail:
                    message = f"{message}: {detail}"
                raise BrowserLaunchError(message)
            try:
                lines = active_port_file.read_text(encoding="utf-8").splitlines()
                if len(lines) >= 2:
                    return f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
            except (FileNotFoundError, OSError, ValueError):
                pass
            await asyncio.sleep(0.05)
        raise BrowserLaunchError(
            f"browser did not expose a CDP endpoint within "
            f"{self.settings.startup_timeout:g} seconds"
        )

    def _require_client(self) -> Client:
        if not self.is_running or self.client is None:
            raise BrowserLaunchError("browser is not running; call start() first")
        return self.client

    async def _cleanup_failed_start(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            self._unmonitor_client(client)
            with contextlib.suppress(Exception):
                await client.__aexit__(None, None, None)
        await self.network.stop()
        await self.console.stop()
        await self.har.stop()
        await self.session.stop()
        await self._terminate_process()
        self._cleanup_profile()
        self.state = BrowserState()

    async def _terminate_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            self._close_stderr()
            return
        if process.returncode is not None:
            self._close_stderr()
            return
        process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self.settings.shutdown_timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
        self._close_stderr()

    def _cleanup_profile(self) -> None:
        if self._temporary_profile is not None:
            self._temporary_profile.cleanup()
            self._temporary_profile = None

    def _read_stderr(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.flush()
        self._stderr.seek(0)
        return self._stderr.read(4096).decode("utf-8", errors="replace").strip()

    def _close_stderr(self) -> None:
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
