"""browser — drive a Chromium tab (navigate, click, type, scroll, tabs, JS)."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from tau.message.types import ImageContent
from tau.tool.render import call_line
from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)

if TYPE_CHECKING:
    from .session import BrowserSession

_EVALUATE_RESULT_LIMIT = 4000


class BrowserAction(str, Enum):
    """Browser automation action."""

    open = "open"
    close = "close"
    navigate = "navigate"
    back = "back"
    forward = "forward"
    refresh = "refresh"
    click = "click"
    type = "type"
    press = "press"
    scroll = "scroll"
    scroll_to_text = "scroll_to_text"
    select = "select"
    switch_tab = "switch_tab"
    close_tab = "close_tab"
    wait = "wait"
    screenshot = "screenshot"
    evaluate = "evaluate"


class MouseButton(str, Enum):
    left = "left"
    right = "right"
    middle = "middle"


class ScrollDirection(str, Enum):
    up = "up"
    down = "down"
    left = "left"
    right = "right"


class BrowserSchema(BaseModel):
    """Input schema for browser; action-specific required fields validated via model_validator."""

    action: BrowserAction = Field(
        description=(
            "Browser action to perform: open (start/attach the browser, required first), "
            "close, navigate, back, forward, refresh, click, type, press, scroll, "
            "scroll_to_text, select (dropdowns), switch_tab, close_tab, wait, "
            "screenshot, or evaluate (run JavaScript)."
        )
    )
    url: str | None = Field(default=None, description="Destination for action=navigate.")
    new_tab: bool = Field(
        default=False,
        description=(
            "Open the destination in a new tab instead of in place. For "
            "action=navigate: the url. For action=click: element_id must be "
            "an <a href=...> link — Chromium doesn't honor middle-click/"
            "Ctrl+click 'open in new tab' for synthetic clicks, so this "
            "resolves the link's href and opens it directly rather than "
            "trying to fake that gesture; not supported with loc clicks."
        ),
    )
    element_id: int | None = Field(
        default=None,
        description=(
            "Element id from the browser-state message (the [123] labels, matching the "
            "boxes on the annotated screenshot). Used by click, type, and select. "
            "Ids change when the page changes — always use ids from the latest state."
        ),
    )
    loc: tuple[float, float] | None = Field(
        default=None,
        description=(
            "Viewport coordinate [x, y] fallback for click and type when no labeled "
            "element fits. Prefer element_id."
        ),
    )
    button: MouseButton = Field(default=MouseButton.left, description="Mouse button for action=click.")
    clicks: int = Field(default=1, ge=1, le=3, description="Click count (2 = double click).")
    text: str | None = Field(
        default=None,
        description="Text to type for action=type, or the text to find for action=scroll_to_text.",
    )
    clear: bool = Field(default=False, description="Clear the field before typing.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")
    is_sensitive: bool = Field(
        default=False,
        description="Mark typed text as sensitive (e.g. passwords) so it is not echoed.",
    )
    keys: str | None = Field(
        default=None,
        description="Key or chord for action=press, e.g. 'Enter', 'Escape', 'Tab', 'CTRL+a'.",
    )
    direction: ScrollDirection = Field(default=ScrollDirection.down, description="Scroll direction.")
    amount: int = Field(default=600, ge=1, le=10000, description="Scroll distance in pixels.")
    option: str | None = Field(
        default=None,
        description=(
            "Visible option text for action=select. Omit to list the dropdown's options instead."
        ),
    )
    target_id: str | None = Field(
        default=None,
        description="Tab target id (from the browser-state tab list) for switch_tab and close_tab.",
    )
    seconds: float = Field(default=1.0, ge=0, le=30, description="Seconds to wait for action=wait.")
    full_page: bool = Field(default=False, description="Capture the full page for action=screenshot.")
    expression: str | None = Field(
        default=None,
        description="JavaScript expression for action=evaluate; its value is returned.",
    )

    @model_validator(mode="after")
    def _check_action_fields(self) -> BrowserSchema:
        if self.action == BrowserAction.navigate and not self.url:
            raise ValueError("'url' is required when action='navigate'")
        if self.action == BrowserAction.click and self.element_id is None and self.loc is None:
            raise ValueError("'element_id' or 'loc' is required when action='click'")
        if self.action == BrowserAction.click and self.new_tab and self.element_id is None:
            raise ValueError("'new_tab' with action='click' requires 'element_id' (a link element), not 'loc'")
        if self.action == BrowserAction.type and self.text is None:
            raise ValueError("'text' is required when action='type'")
        if self.action == BrowserAction.press and not self.keys:
            raise ValueError("'keys' is required when action='press'")
        if self.action == BrowserAction.scroll_to_text and not self.text:
            raise ValueError("'text' is required when action='scroll_to_text'")
        if self.action == BrowserAction.select and self.element_id is None and self.loc is None:
            raise ValueError("'element_id' or 'loc' is required when action='select'")
        if self.action in {BrowserAction.switch_tab, BrowserAction.close_tab} and not self.target_id:
            raise ValueError("'target_id' is required for this action")
        if self.action == BrowserAction.evaluate and not self.expression:
            raise ValueError("'expression' is required when action='evaluate'")
        return self


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    action = args.get("action", "")
    detail = {
        "navigate": args.get("url", ""),
        "type": "***" if args.get("is_sensitive") else args.get("text", ""),
        "press": args.get("keys", ""),
        "scroll_to_text": args.get("text", ""),
        "select": args.get("option", ""),
        "evaluate": args.get("expression", ""),
    }.get(action, "")
    return call_line("browser", action, detail)


class BrowserTool(Tool):
    """Browser automation tool driving a Chromium tab over CDP."""

    def __init__(self, session: BrowserSession) -> None:
        self._session = session
        super().__init__(
            name="browser",
            description=(
                "Control a Chromium browser through one action-based tool. "
                "Use open to start or attach the browser (required before any other "
                "action) and close to shut it down. Use navigate/back/forward/refresh "
                "to move between pages, click/type/press/scroll/scroll_to_text/select "
                "to interact, switch_tab/close_tab to manage tabs, wait for delays, "
                "screenshot for an on-demand capture, and evaluate to run JavaScript "
                "on the page."
            ),
            schema=BrowserSchema,
            kind=ToolKind.Execute,
            render_call=_render_call,
            prompt_guidelines=(
                "Call action='open' once before any other browser action and "
                "action='close' when finished. While the browser is open, a fresh "
                "browser-state message (tabs, URL, interactive elements, and/or an "
                "annotated screenshot) is injected automatically every turn — do not "
                "take screenshots just to see the page. Interact via the [id] labels "
                "from that state (element_id); fall back to loc coordinates only when "
                "nothing is labeled. Ids are stale after the page changes, so always "
                "use the latest state. Set is_sensitive=true when typing secrets. "
                "Stop and ask the user at login walls instead of guessing credentials, "
                "and prefer normal fetch tools over the browser for plain HTTP "
                "downloads."
            ),
        )

    def get_display_name(self, args: dict) -> str:
        action = args.get("action", "")
        if action == "open":
            return "Opening browser"
        if action == "close":
            return "Closing browser"
        if action == "navigate":
            url = args.get("url") or ""
            return f"Navigating: {url[:50]}" if url else "Navigating"
        if action == "click":
            element_id = args.get("element_id")
            return f"Clicking [{element_id}]" if element_id is not None else "Clicking"
        if action == "type":
            if args.get("is_sensitive"):
                return "Typing (sensitive)"
            text = args.get("text") or ""
            return f"Typing: {text[:30]}" if text else "Typing"
        if action == "press":
            return f"Pressing {args.get('keys', '')}"
        if action == "scroll":
            return f"Scrolling {args.get('direction', 'down')}"
        if action == "scroll_to_text":
            return f"Scrolling to: {(args.get('text') or '')[:30]}"
        if action == "select":
            option = args.get("option")
            return f"Selecting: {option[:30]}" if option else "Listing options"
        if action == "switch_tab":
            return "Switching tab"
        if action == "close_tab":
            return "Closing tab"
        if action == "wait":
            return f"Waiting {args.get('seconds', 1)}s"
        if action == "screenshot":
            return "Taking screenshot"
        if action == "evaluate":
            return "Running JavaScript"
        if action in {"back", "forward", "refresh"}:
            return action.capitalize()
        return "Browser"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        try:
            params = BrowserSchema.model_validate(invocation.params)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"browser: {exc}")

        if signal is not None and signal.is_set():
            return ToolResult.error(id=invocation.id, content="browser: cancelled")

        session = self._session
        try:
            if params.action == BrowserAction.open:
                message = await session.open()
                return ToolResult.ok(id=invocation.id, content=message)

            if params.action == BrowserAction.close:
                if not session.is_open:
                    return ToolResult.ok(id=invocation.id, content="Browser is not open.")
                await session.close()
                return ToolResult.ok(id=invocation.id, content="Browser closed.")

            if not session.is_open:
                return ToolResult.error(
                    id=invocation.id,
                    content="Browser is not open. Use action='open' first.",
                )

            return await self._run_action(invocation.id, params)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"browser: {exc}")

    async def _run_action(self, call_id: str, params: BrowserSchema) -> ToolResult:
        session = self._session
        browser = session.browser
        session_id = await session.ensure_page()

        if params.action == BrowserAction.navigate:
            assert params.url is not None
            result = await browser.navigate(session_id, params.url, new_tab=params.new_tab)
            if params.new_tab and result.get("targetId"):
                await session.adopt_target(result["targetId"])
            status = result.get("status")
            suffix = f" (HTTP {status})" if status is not None else ""
            where = " in a new tab" if params.new_tab else ""
            return ToolResult.ok(call_id, f"Navigated to {params.url}{where}{suffix}.")

        if params.action == BrowserAction.back:
            moved = await browser.go_back(session_id)
            return ToolResult.ok(call_id, "Went back." if moved else "No previous page in history.")

        if params.action == BrowserAction.forward:
            moved = await browser.go_forward(session_id)
            return ToolResult.ok(call_id, "Went forward." if moved else "No next page in history.")

        if params.action == BrowserAction.refresh:
            await browser.refresh(session_id)
            return ToolResult.ok(call_id, "Page refreshed.")

        if params.action == BrowserAction.click:
            if params.element_id is not None:
                element = await self._require_element(params.element_id)
                new_page = await browser.click_element(
                    session_id, element,
                    button=params.button.value, click_count=params.clicks,
                    new_tab=params.new_tab,
                )
                label = (element.text or "").strip().replace("\n", " ")[:40]
                if params.new_tab and new_page is not None:
                    await session.adopt_target(new_page.target_id)
                    return ToolResult.ok(
                        call_id,
                        f"Opened [{params.element_id}] <{element.tag_name}> {label!r} in a new tab.",
                    )
                return ToolResult.ok(
                    call_id,
                    f"Clicked [{params.element_id}] <{element.tag_name}> {label!r}.",
                )
            assert params.loc is not None
            x, y = params.loc
            await browser.click(
                session_id, x, y, button=params.button.value, click_count=params.clicks
            )
            return ToolResult.ok(call_id, f"Clicked at ({x:g}, {y:g}).")

        if params.action == BrowserAction.type:
            assert params.text is not None
            shown = "(sensitive text)" if params.is_sensitive else repr(params.text[:60])
            if params.element_id is not None:
                element = await self._require_element(params.element_id)
                await browser.type_into_element(
                    session_id, element, params.text,
                    clear=params.clear, is_sensitive=params.is_sensitive,
                )
                target = f"element [{params.element_id}]"
            elif params.loc is not None:
                x, y = params.loc
                await browser.type_text(
                    session_id, params.text, x=x, y=y,
                    clear=params.clear, is_sensitive=params.is_sensitive,
                )
                target = f"({x:g}, {y:g})"
            else:
                await browser.type_text(
                    session_id, params.text,
                    clear=params.clear, is_sensitive=params.is_sensitive,
                )
                target = "the focused element"
            if params.press_enter:
                await browser.press(session_id, "Enter")
            enter = " and pressed Enter" if params.press_enter else ""
            return ToolResult.ok(call_id, f"Typed {shown} into {target}{enter}.")

        if params.action == BrowserAction.press:
            assert params.keys is not None
            await browser.press(session_id, params.keys)
            return ToolResult.ok(call_id, f"Pressed {params.keys}.")

        if params.action == BrowserAction.scroll:
            x, y = self._viewport_center()
            await browser.scroll(
                session_id, params.direction.value, params.amount, x=x, y=y
            )
            return ToolResult.ok(
                call_id, f"Scrolled {params.direction.value} {params.amount}px."
            )

        if params.action == BrowserAction.scroll_to_text:
            assert params.text is not None
            found = await browser.scroll_to_text(session_id, params.text)
            if not found:
                return ToolResult.error(call_id, f"Text {params.text!r} not found on the page.")
            return ToolResult.ok(call_id, f"Scrolled to {params.text!r}.")

        if params.action == BrowserAction.select:
            x, y = await self._select_coords(params)
            if params.option is None:
                options = await browser.get_dropdown_options(session_id, x, y)
                lines = [
                    f"- {option['text']!r}"
                    + (" (selected)" if option.get("selected") else "")
                    + (" (disabled)" if option.get("disabled") else "")
                    for option in options
                ]
                return ToolResult.ok(call_id, "Dropdown options:\n" + "\n".join(lines))
            selected = await browser.select_dropdown_option(session_id, x, y, params.option)
            if not selected:
                return ToolResult.error(
                    call_id,
                    f"Option {params.option!r} not found — use action='select' without "
                    "'option' to list the available options.",
                )
            return ToolResult.ok(call_id, f"Selected {params.option!r}.")

        if params.action == BrowserAction.switch_tab:
            assert params.target_id is not None
            await session.switch_tab(params.target_id)
            return ToolResult.ok(call_id, f"Switched to tab {params.target_id}.")

        if params.action == BrowserAction.close_tab:
            assert params.target_id is not None
            closed = await session.close_tab(params.target_id)
            return ToolResult.ok(
                call_id,
                f"Closed tab {params.target_id}." if closed else f"Could not close tab {params.target_id}.",
            )

        if params.action == BrowserAction.wait:
            await browser.wait(params.seconds)
            return ToolResult.ok(call_id, f"Waited {params.seconds:g}s.")

        if params.action == BrowserAction.screenshot:
            await session.clear_highlight()
            data = await browser.screenshot(session_id, full_page=params.full_page)
            return ToolResult.ok(
                call_id,
                "Screenshot captured.",
                image=ImageContent(images=[data]),
            )

        if params.action == BrowserAction.evaluate:
            assert params.expression is not None
            value = await browser.evaluate(session_id, params.expression)
            try:
                rendered = json.dumps(value, ensure_ascii=False, default=repr)
            except (TypeError, ValueError):
                rendered = repr(value)
            if len(rendered) > _EVALUATE_RESULT_LIMIT:
                rendered = rendered[:_EVALUATE_RESULT_LIMIT] + "… (truncated)"
            return ToolResult.ok(call_id, rendered)

        return ToolResult.error(call_id, f"Unknown action: {params.action.value}")

    async def _require_element(self, element_id: int):
        element = await self._session.element(element_id)
        if element is None:
            raise ValueError(
                f"element_id {element_id} is not in the current page state — ids "
                "change when the page changes; use one from the latest browser-state "
                "message."
            )
        return element

    async def _select_coords(self, params: BrowserSchema) -> tuple[float, float]:
        if params.element_id is not None:
            element = await self._require_element(params.element_id)
            return element.bounds.center
        assert params.loc is not None
        return params.loc

    def _viewport_center(self) -> tuple[float, float]:
        state = self._session.last_state
        if state is not None and getattr(state, "viewport", None) is not None:
            return state.viewport.width / 2, state.viewport.height / 2
        return 400.0, 300.0
