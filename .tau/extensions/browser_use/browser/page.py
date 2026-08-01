"""Target-bound page actions."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from ..dom.types import Element

from .types import LoadState, SessionID, TargetID

if TYPE_CHECKING:
    from .download import Download
    from .network import Request
    from .service import Browser
    from .state import PageState


class Page:
    def __init__(self, browser: Browser, target_id: TargetID) -> None:
        self.browser = browser
        self.target_id = target_id

    @property
    def target(self):
        return self.browser.session.targets.get(self.target_id)

    @property
    def url(self) -> str:
        target = self.target
        return target.url if target is not None else ""

    @property
    def title(self) -> str:
        target = self.target
        return target.title if target is not None else ""

    @property
    def is_closed(self) -> bool:
        return self.target_id not in self.browser.session.targets

    async def session_id(self) -> SessionID:
        return await self.browser.session.session_for(self.target_id)

    async def activate(self) -> None:
        await self.browser.session.activate(self.target_id)

    async def close(self) -> bool:
        return await self.browser.close_page(self)

    async def navigate(
        self,
        url: str,
        *,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return await self.browser.navigate(
            await self.session_id(),
            url,
            wait_until=wait_until,
            timeout=timeout,
        )

    async def wait_for_load_state(
        self,
        state: LoadState = "load",
        *,
        timeout: float = 30.0,
    ) -> None:
        await self.browser.session.wait_for_load_state(
            await self.session_id(),
            state,
            timeout,
        )

    async def wait_for_navigation(
        self,
        *,
        wait_until: LoadState = "load",
        timeout: float = 30.0,
    ) -> str:
        return await self.browser.session.wait_for_navigation(
            await self.session_id(),
            wait_until=wait_until,
            timeout=timeout,
        )

    async def wait_for_element(
        self,
        predicate: Any = None,
        *,
        text: str | None = None,
        tag_name: str | None = None,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Element:
        return await self.browser.wait_for_element(
            await self.session_id(),
            predicate,
            text=text,
            tag_name=tag_name,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def wait_for_text(
        self,
        text: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Element:
        return await self.browser.wait_for_text(
            await self.session_id(),
            text,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def wait_for_url(
        self,
        pattern: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> str:
        return await self.browser.wait_for_url(
            await self.session_id(),
            pattern,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def wait_for_function(
        self,
        expression: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Any:
        return await self.browser.wait_for_function(
            await self.session_id(),
            expression,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def requests(self) -> list[Request]:
        return self.browser.network.for_session(await self.session_id())

    async def click(
        self,
        x: float,
        y: float,
        *,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
    ) -> None:
        await self.browser.click(
            await self.session_id(),
            x,
            y,
            button=button,
            click_count=click_count,
        )

    async def click_element(
        self,
        element: Element,
        *,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
        new_tab: bool = False,
    ) -> Page | None:
        return await self.browser.click_element(
            await self.session_id(),
            element,
            button=button,
            click_count=click_count,
            new_tab=new_tab,
        )

    async def hover(self, x: float, y: float) -> None:
        await self.browser.hover(await self.session_id(), x, y)

    async def drag(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        button: Literal["left", "right", "middle"] = "left",
        steps: int = 10,
    ) -> None:
        await self.browser.drag(
            await self.session_id(),
            start,
            end,
            button=button,
            steps=steps,
        )

    async def type_text(
        self,
        text: str,
        *,
        x: float | None = None,
        y: float | None = None,
        clear: bool = False,
        is_sensitive: bool = False,
        sensitive_key_name: str | None = None,
    ) -> None:
        await self.browser.type_text(
            await self.session_id(),
            text,
            x=x,
            y=y,
            clear=clear,
            is_sensitive=is_sensitive,
            sensitive_key_name=sensitive_key_name,
        )

    async def send_keys(self, keys: str) -> None:
        await self.browser.send_keys(await self.session_id(), keys)

    async def press(self, key: str) -> None:
        await self.browser.press(await self.session_id(), key)

    async def key_down(self, key: str) -> None:
        await self.browser.key_down(await self.session_id(), key)

    async def key_up(self, key: str) -> None:
        await self.browser.key_up(await self.session_id(), key)

    async def type_into_element(
        self,
        element: Element,
        text: str,
        *,
        clear: bool = False,
        is_sensitive: bool = False,
        sensitive_key_name: str | None = None,
    ) -> None:
        await self.browser.type_into_element(
            await self.session_id(),
            element,
            text,
            clear=clear,
            is_sensitive=is_sensitive,
            sensitive_key_name=sensitive_key_name,
        )

    async def scroll_into_view(self, element: Element) -> None:
        await self.browser.scroll_into_view(await self.session_id(), element)

    async def scroll(
        self,
        direction: Literal["up", "down", "left", "right"],
        amount: int,
        *,
        x: float = 0,
        y: float = 0,
    ) -> None:
        await self.browser.scroll(await self.session_id(), direction, amount, x=x, y=y)

    async def go_back(self) -> bool:
        return await self.browser.go_back(await self.session_id())

    async def go_forward(self) -> bool:
        return await self.browser.go_forward(await self.session_id())

    async def refresh(self, *, ignore_cache: bool = False) -> None:
        await self.browser.refresh(await self.session_id(), ignore_cache=ignore_cache)

    async def screenshot(
        self,
        *,
        full_page: bool = False,
        clip: dict[str, float] | None = None,
        format: Literal["png", "jpeg", "webp"] = "png",
        quality: int | None = None,
    ) -> str:
        return await self.browser.screenshot(
            await self.session_id(),
            full_page=full_page,
            clip=clip,
            format=format,
            quality=quality,
        )

    async def upload_file(self, x: float, y: float, file_path: str | Path) -> None:
        await self.browser.upload_file(await self.session_id(), x, y, file_path)

    async def get_dropdown_options(self, x: float, y: float) -> list[dict[str, Any]]:
        return await self.browser.get_dropdown_options(await self.session_id(), x, y)

    async def select_dropdown_option(self, x: float, y: float, text: str) -> bool:
        return await self.browser.select_dropdown_option(
            await self.session_id(), x, y, text
        )

    async def scroll_to_text(
        self, text: str, *, direction: Literal["up", "down"] = "down"
    ) -> bool:
        return await self.browser.scroll_to_text(
            await self.session_id(), text, direction=direction
        )

    async def evaluate(self, expression: str) -> Any:
        return await self.browser.evaluate(await self.session_id(), expression)

    async def state(self) -> dict[str, Any]:
        return await self.browser.browser_state(await self.session_id())

    async def get_state(
        self,
        *,
        include_dom: bool = True,
        include_screenshot: bool = True,
        include_accessibility: bool = True,
        force_dom_refresh: bool = False,
    ) -> PageState:
        return await self.browser.get_page_state(
            await self.session_id(),
            include_dom=include_dom,
            include_screenshot=include_screenshot,
            include_accessibility=include_accessibility,
            force_dom_refresh=force_dom_refresh,
        )

    async def expect_download(
        self,
        action: Callable[[], Awaitable[Any] | Any],
        *,
        timeout: float = 30.0,
    ) -> Download:
        return await self.browser.expect_download(
            action,
            target_id=self.target_id,
            timeout=timeout,
        )
