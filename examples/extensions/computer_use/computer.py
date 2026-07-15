"""computer — control the local desktop (click, type, scroll, window management, screenshots)."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

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
    from .types import Desktop


class ComputerAction(str, Enum):
    """Desktop automation action: open, close, click, type, wait, app, scroll, move, drag, shortcut."""

    open = "open"
    close = "close"
    click = "click"
    type = "type"
    wait = "wait"
    app = "app"
    scroll = "scroll"
    move = "move"
    drag = "drag"
    shortcut = "shortcut"


class AppMode(str, Enum):
    launch = "launch"
    switch = "switch"
    resize = "resize"
    move = "move"


class MouseButton(str, Enum):
    left = "left"
    right = "right"
    middle = "middle"


class ScrollDirection(str, Enum):
    up = "up"
    down = "down"
    left = "left"
    right = "right"


class CaretPosition(str, Enum):
    start = "start"
    idle = "idle"
    end = "end"


class ComputerSchema(BaseModel):
    """Input schema for computer; action-specific required fields validated via model_validator."""

    action: ComputerAction = Field(
        description=(
            "Computer action to perform: open (enable desktop access), "
            "close (release desktop access), click, type, wait, app, "
            "scroll, move, drag, or shortcut."
        )
    )
    loc: tuple[int, int] | None = Field(
        default=None,
        description=(
            "Target screen coordinate as [x, y]. Required for click, type, move, and drag "
            "(type clicks this location to focus it before sending keystrokes), and for "
            "app with app_mode=move."
        ),
    )
    text: str | None = Field(default=None, description="Text to type. Required for action=type.")
    duration: float = Field(default=1.0, ge=0, le=60, description="Seconds to wait for action=wait.")
    button: MouseButton = Field(default=MouseButton.left, description="Mouse button for action=click.")
    clicks: int = Field(default=1, ge=0, le=3, description="Number of clicks. Use 0 to only move the pointer.")
    clear: bool = Field(default=False, description="Clear focused text before typing.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")
    caret_position: CaretPosition = Field(default=CaretPosition.idle, description="Caret movement before typing.")
    app_mode: AppMode = Field(default=AppMode.launch, description="Application operation for action=app.")
    name: str | None = Field(default=None, description="Application name for action=app.")
    size: tuple[int, int] | None = Field(default=None, description="Window size as [width, height] for app resize.")
    direction: ScrollDirection = Field(default=ScrollDirection.down, description="Scroll direction.")
    wheel_times: int = Field(default=1, ge=1, le=20, description="Number of wheel ticks for action=scroll.")
    shortcut: str | None = Field(default=None, description="Keyboard shortcut such as command+c or ctrl+c.")

    @model_validator(mode="after")
    def _check_action_fields(self) -> ComputerSchema:
        if (
            self.action in {ComputerAction.click, ComputerAction.type, ComputerAction.move, ComputerAction.drag}
            and self.loc is None
        ):
            raise ValueError("'loc' is required for this action")
        if self.action == ComputerAction.type and self.text is None:
            raise ValueError("'text' is required when action='type'")
        if self.action == ComputerAction.shortcut and not self.shortcut:
            raise ValueError("'shortcut' is required when action='shortcut'")
        if self.action == ComputerAction.app:
            if self.app_mode in {AppMode.launch, AppMode.switch} and not self.name:
                raise ValueError("'name' is required when action='app' with launch or switch")
            if self.app_mode == AppMode.resize and self.size is None:
                raise ValueError("'size' is required when action='app' with resize")
            if self.app_mode == AppMode.move and self.loc is None:
                raise ValueError("'loc' is required when action='app' with move")
        return self


def _render_call(args: dict, _streaming: bool = False) -> list[str]:
    action = args.get("action", "")
    detail = {
        "type": args.get("text", ""),
        "app": args.get("name", ""),
        "shortcut": args.get("shortcut", ""),
    }.get(action, "")
    return call_line("computer", action, detail)


class ComputerTool(Tool):
    """Desktop automation tool that drives mouse, keyboard, and application management."""

    def __init__(self, desktop: Desktop) -> None:
        self._desktop = desktop
        super().__init__(
            name="computer",
            description=(
                "Control the local desktop through one action-based computer tool. "
                "Use open to enable desktop access (required before any other action) and "
                "close to release it. Use click/type/scroll/move/drag for pointer and text input, "
                "shortcut for keyboard shortcuts, wait for delays, and app for "
                "launching, switching, resizing, or moving applications."
            ),
            schema=ComputerSchema,
            kind=ToolKind.Execute,
            render_call=_render_call,
            prompt_guidelines=(
                "Call action='open' once before any other computer action, and action='close' "
                "when finished controlling the desktop. Prefer the dedicated read/write/edit/"
                "terminal tools over the computer tool for anything achievable without a GUI."
            ),
        )

    def get_display_name(self, args: dict) -> str:
        action = args.get("action", "")
        text = args.get("text") or ""
        name = args.get("name") or ""
        app_mode = args.get("app_mode") or ""
        shortcut = args.get("shortcut") or ""
        duration = args.get("duration", "")
        if action == "open":
            return "Opening desktop"
        if action == "close":
            return "Closing desktop"
        if action == "click":
            return "Clicking"
        if action == "type":
            return f"Typing: {text[:30]}" if text else "Typing"
        if action == "wait":
            return f"Waiting {duration}s" if duration else "Waiting"
        if action == "app":
            if app_mode == "launch":
                return f"Launching: {name}" if name else "Launching app"
            if app_mode == "switch":
                return f"Switching to: {name}" if name else "Switching app"
            return f"App: {app_mode}" if app_mode else "App"
        if action == "scroll":
            return "Scrolling"
        if action == "move":
            return "Moving cursor"
        if action == "drag":
            return "Dragging"
        if action == "shortcut":
            return f"Shortcut: {shortcut}" if shortcut else "Shortcut"
        return "Computer"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        try:
            params = ComputerSchema.model_validate(invocation.params)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"computer: {exc}")

        async def update(text: str) -> None:
            if tool_execution_update_callback is not None:
                await tool_execution_update_callback(ToolResult.ok(invocation.id, text))

        desktop = self._desktop
        try:
            if params.action == ComputerAction.open:
                if desktop.is_open:
                    return ToolResult.ok(id=invocation.id, content="Desktop is already open.")
                await update("Opening desktop…")
                desktop.open()
                return ToolResult.ok(id=invocation.id, content="Desktop access enabled.")

            if params.action == ComputerAction.close:
                if not desktop.is_open:
                    return ToolResult.ok(id=invocation.id, content="Desktop is not open.")
                await update("Closing desktop…")
                desktop.close()
                return ToolResult.ok(id=invocation.id, content="Desktop access released.")

            if not desktop.is_open:
                return ToolResult.error(
                    id=invocation.id,
                    content="Desktop is not accessible. Use action='open' to enable desktop control first.",
                )

            await self._announce(update, params)
            content = self._run_action(desktop, params)
            return ToolResult.ok(id=invocation.id, content=content)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"computer: {exc}")

    async def _announce(self, update, params: ComputerSchema) -> None:
        loc = self._loc(params)
        if params.action == ComputerAction.click:
            coord = f"({loc[0]}, {loc[1]})" if loc else ""
            clicks = f" ×{params.clicks}" if params.clicks > 1 else ""
            await update(f"Clicking {params.button.value}{clicks} at {coord}…")
        elif params.action == ComputerAction.type:
            preview = (params.text or "")[:40]
            preview += "…" if len(params.text or "") > 40 else ""
            await update(f'Typing "{preview}"…')
        elif params.action == ComputerAction.wait:
            await update(f"Waiting {params.duration:g}s…")
        elif params.action == ComputerAction.app:
            label = params.name or params.app_mode.value
            await update(f"App {params.app_mode.value}: {label}…")
        elif params.action == ComputerAction.scroll:
            await update(f"Scrolling {params.direction.value} {params.wheel_times}×…")
        elif params.action == ComputerAction.move:
            coord = f"({loc[0]}, {loc[1]})" if loc else ""
            await update(f"Moving pointer to {coord}…")
        elif params.action == ComputerAction.drag:
            coord = f"({loc[0]}, {loc[1]})" if loc else ""
            await update(f"Dragging to {coord}…")
        elif params.action == ComputerAction.shortcut:
            await update(f"Shortcut {params.shortcut}…")

    def _run_action(self, desktop: Desktop, params: ComputerSchema) -> str:
        loc = self._loc(params)
        if params.action == ComputerAction.click:
            assert loc is not None
            desktop.click(loc, button=params.button.value, clicks=params.clicks)
            return f"Clicked {params.button.value} at {loc[0]},{loc[1]}."
        if params.action == ComputerAction.type:
            assert loc is not None
            desktop.type(
                loc,
                text=params.text or "",
                caret_position=params.caret_position.value,
                clear=params.clear,
                press_enter=params.press_enter,
            )
            return "Typed text."
        if params.action == ComputerAction.wait:
            desktop.wait(params.duration)
            return f"Waited {params.duration:g} seconds."
        if params.action == ComputerAction.app:
            result = desktop.app(mode=params.app_mode.value, name=params.name, loc=loc, size=params.size)
            return str(result or "App action completed.")
        if params.action == ComputerAction.scroll:
            desktop.scroll(
                loc=loc,
                direction=params.direction.value,
                wheel_times=params.wheel_times,
            )
            return "Scrolled."
        if params.action == ComputerAction.move:
            assert loc is not None
            desktop.move(loc)
            return f"Moved pointer to {loc[0]},{loc[1]}."
        if params.action == ComputerAction.drag:
            assert loc is not None
            desktop.drag(loc)
            return f"Dragged pointer to {loc[0]},{loc[1]}."
        if params.action == ComputerAction.shortcut:
            desktop.shortcut(params.shortcut or "")
            return f"Pressed shortcut {params.shortcut}."
        return f"Unknown action: {params.action.value}"

    def _loc(self, params: ComputerSchema) -> tuple[int, int] | None:
        return params.loc
