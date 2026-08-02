"""Owns the Browser instance and the currently controlled tab for the tau extension.

The Browser project's packages (``browser``, ``dom``, ``cdp``, ``watchdog``,
``hooks``) are vendored directly alongside this file and imported relative to
this extension's own package, so no sys.path setup is needed for them.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .browser import Browser
    from .dom.types import Element


def _resolve_ws_url(value: str, timeout: float = 10.0) -> str:
    """Turn a user-supplied CDP endpoint (bare port, http URL, or ws URL)
    into the websocket debugger URL BrowserSettings.cdp_url expects.

    Blocking (urllib) — call via asyncio.to_thread.
    """
    value = value.strip()
    if value.startswith(("ws://", "wss://")):
        return value
    if value.isdigit():
        # Chrome binds the debug port on IPv4 and/or IPv6 loopback, and when
        # two Chrome instances race for the port each can end up holding one
        # family (the losing side answers 404s) — so probe both.
        bases = [f"http://127.0.0.1:{value}", f"http://[::1]:{value}"]
    else:
        bases = [value.rstrip("/")]
    urls = [
        base if base.endswith("/json/version") else f"{base}/json/version"
        for base in bases
    ]

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    info = json.loads(response.read())
                return info["webSocketDebuggerUrl"]
            except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
                last_error = exc
        time.sleep(0.5)
    raise RuntimeError(
        f"could not reach a Chrome debug endpoint at {' or '.join(urls)}: "
        f"{last_error}. Make sure Chrome was launched with "
        "--remote-debugging-port."
    )


_HIGHLIGHT_DURATION_MS = 5000
_HIGHLIGHT_PALETTE = ("#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#00897b")
_CLEAR_HIGHLIGHT_JS = (
    "(() => { const p = document.getElementById('__tau_highlight__');"
    " if (p) p.remove(); })()"
)


def _highlight_expression(elements: tuple[Any, ...], duration_ms: int) -> str:
    """JS that flashes labeled bounding boxes over the clickable elements as a
    pointer-events:none overlay, self-removing after `duration_ms`.

    Boxes are anchored at *document* coordinates inside an absolutely
    positioned host, so they travel with the content when the user scrolls
    mid-flash. A window resize reflows the page and invalidates every
    captured coordinate, so the overlay removes itself immediately on resize
    rather than display boxes against a layout that no longer exists (the
    next capture redraws them from fresh bounds).

    Same palette/label scheme as the annotated screenshot, so what the human
    sees flash in the live page matches what the model sees in its image.
    The overlay never intercepts input (elementFromPoint skips
    pointer-events:none nodes), so hit validation and clicks are unaffected.
    """
    boxes = [
        {
            "i": element.backend_node_id,
            "x": round(element.bounds.document_x, 1),
            "y": round(element.bounds.document_y, 1),
            "w": round(element.bounds.width, 1),
            "h": round(element.bounds.height, 1),
            "c": _HIGHLIGHT_PALETTE[index % len(_HIGHLIGHT_PALETTE)],
        }
        for index, element in enumerate(
            element for element in elements if element.clickable
        )
    ]
    return f"""
        (() => {{
            const prev = document.getElementById('__tau_highlight__');
            if (prev) prev.remove();
            const boxes = {json.dumps(boxes)};
            if (!boxes.length) return 0;
            const host = document.createElement('div');
            host.id = '__tau_highlight__';
            host.style.cssText =
                'position:absolute;left:0;top:0;width:0;height:0;' +
                'pointer-events:none;z-index:2147483647;';
            for (const b of boxes) {{
                const box = document.createElement('div');
                box.style.cssText =
                    `position:absolute;left:${{b.x}}px;top:${{b.y}}px;` +
                    `width:${{b.w}}px;height:${{b.h}}px;` +
                    `border:1px solid ${{b.c}};box-sizing:border-box;`;
                const label = document.createElement('span');
                label.textContent = b.i;
                label.style.cssText =
                    `position:absolute;left:-1px;background:${{b.c}};color:#fff;` +
                    'font:10px/12px monospace;padding:0 3px;white-space:nowrap;';
                label.style.top = b.y < 14 ? '0' : '-13px';
                box.appendChild(label);
                host.appendChild(box);
            }}
            document.documentElement.appendChild(host);
            const cleanup = () => {{
                host.remove();
                removeEventListener('resize', cleanup);
            }};
            addEventListener('resize', cleanup);
            setTimeout(cleanup, {duration_ms});
            return boxes.length;
        }})()
    """


class BrowserSession:
    """Lazily-started Browser plus the tab the agent is currently driving.

    Nothing heavy is imported or launched at construction time — the Browser
    packages load and Chromium starts only on the first `open()`, keeping
    tau's extension-load path (and sessions that never touch the browser)
    cheap, the same way computer_use defers its desktop backend.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        cdp_url: str | None = None,
        highlight: bool = True,
        user_data_dir: str | None = None,
        stealth: bool = False,
    ) -> None:
        self._headless = headless
        self._cdp_url = cdp_url
        self._highlight = highlight
        self._user_data_dir = user_data_dir
        self._stealth = stealth
        self._browser: Browser | None = None
        self._target_id: str | None = None
        self._session_id: str | None = None
        self._elements: dict[int, Element] = {}
        self.last_state: Any = None

    @property
    def is_open(self) -> bool:
        return self._browser is not None and self._browser.is_running

    @property
    def browser(self) -> Browser:
        if self._browser is None or not self._browser.is_running:
            raise RuntimeError("browser is not open — use action='open' first")
        return self._browser

    async def open(self) -> str:
        if self.is_open:
            return "Browser is already open."
        import asyncio

        from .browser import Browser, BrowserSettings

        profile = None
        if self._cdp_url:
            ws_url = await asyncio.to_thread(_resolve_ws_url, self._cdp_url)
            settings = BrowserSettings(cdp_url=ws_url)
            how = f"attached to existing browser at {ws_url}"
        else:
            if self._user_data_dir:
                profile = str(Path(self._user_data_dir).expanduser())
            settings = BrowserSettings(
                headless=self._headless,
                user_data_dir=profile,
                stealth=self._stealth,
            )
            how = f"launched a {'headless' if self._headless else 'headed'} Chromium"
            if profile:
                how += f" (persistent profile {profile})"

        browser = Browser(settings)
        await browser.start()
        self._browser = browser
        await self.ensure_page()
        if not self._cdp_url and browser.state.remote:
            how = f"reused an already-running Chromium (persistent profile {profile})"
        return f"Browser open ({how})."

    async def close(self) -> None:
        browser, self._browser = self._browser, None
        self._target_id = None
        self._session_id = None
        self._elements = {}
        self.last_state = None
        if browser is not None:
            await browser.stop()

    async def ensure_page(self) -> str:
        """Return a CDP session id for the current tab, adopting the most
        recent page (or creating a blank one) if the tracked tab is gone."""
        browser = self.browser
        pages = await browser.pages()
        alive = {page.target_id for page in pages}
        if self._target_id not in alive:
            page = pages[-1] if pages else await browser.new_page("about:blank")
            self._target_id = page.target_id
            self._session_id = None
        if self._session_id is None:
            assert self._target_id is not None
            self._session_id = await browser.attach_page(self._target_id)
        return self._session_id

    async def capture(self, *, include_screenshot: bool) -> Any:
        """Fresh PageState for the current tab; refreshes the element cache
        the tool uses to resolve element_id references.

        Any live highlight overlay is removed *before* the capture (so the
        model's screenshot and DOM never contain it) and redrawn from the
        fresh element set afterwards, flashing for a few seconds so a human
        watching a headed browser sees what the agent currently sees."""
        session_id = await self.ensure_page()
        await self.clear_highlight()
        state = await self.browser.get_page_state(
            session_id, include_screenshot=include_screenshot
        )
        self._elements = {
            element.backend_node_id: element for element in state.elements
        }
        self.last_state = state
        if self._highlight and state.elements:
            with contextlib.suppress(Exception):
                await self.browser.evaluate(
                    session_id,
                    _highlight_expression(state.elements, _HIGHLIGHT_DURATION_MS),
                )
        return state

    async def clear_highlight(self) -> None:
        """Remove the highlight overlay immediately (best-effort)."""
        with contextlib.suppress(Exception):
            if self._session_id is not None:
                await self.browser.evaluate(self._session_id, _CLEAR_HIGHLIGHT_JS)

    async def element(self, element_id: int) -> Element | None:
        """Resolve an element_id from the last observation, re-capturing once
        if it is missing (the page may have changed since the model looked)."""
        element = self._elements.get(element_id)
        if element is None:
            await self.capture(include_screenshot=False)
            element = self._elements.get(element_id)
        return element

    async def tabs(self) -> list[tuple[str, str, str, bool]]:
        """(target_id, url, title, is_current) for every open tab."""
        pages = await self.browser.pages()
        return [
            (page.target_id, page.url, page.title, page.target_id == self._target_id)
            for page in pages
        ]

    async def switch_tab(self, target_id: str) -> None:
        self._session_id = await self.browser.switch_tab(target_id)
        self._target_id = target_id
        self._elements = {}

    async def close_tab(self, target_id: str) -> bool:
        if len(await self.browser.pages()) <= 1:
            # Closing the *agent's own* last tab shouldn't be able to take
            # down the whole Chromium process — Chrome can exit within
            # milliseconds of hitting zero tabs, so the replacement has to
            # exist *before* the old tab closes, not be reactively created
            # after (that races the process teardown and reliably loses
            # it). Scoped to this explicit close_tab call only — not a
            # blanket "always keep one tab alive" watchdog — so a human
            # manually closing tabs/the window is respected instead of
            # being fought with an auto-reopened blank tab.
            await self.browser.new_page("about:blank")
        closed = await self.browser.close_tab(target_id)
        if closed and target_id == self._target_id:
            self._target_id = None
            self._session_id = None
            self._elements = {}
        return closed

    async def adopt_target(self, target_id: str) -> None:
        """Start driving a tab the browser just created (e.g. navigate new_tab)."""
        self._target_id = target_id
        self._session_id = await self.browser.attach_page(target_id)
        self._elements = {}
