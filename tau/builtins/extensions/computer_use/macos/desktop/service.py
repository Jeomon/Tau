"""macOS Desktop implementation built on the ax accessibility bindings."""

from __future__ import annotations

import io
import time
from typing import Literal

from ...types import Desktop as DesktopBase
from ...types import DesktopState, Size, Window, WindowStatus
from .. import ax
from ..tree import Tree
from .config import BROWSER_BUNDLE_IDS

_STATUS_MAP: dict[str, WindowStatus] = {
    "Active": WindowStatus.Active,
    "Fullscreen": WindowStatus.Fullscreen,
    "Visible": WindowStatus.Visible,
    "Hidden": WindowStatus.Hidden,
    "Minimized": WindowStatus.Minimized,
    "Windowless": WindowStatus.Windowless,
}


def _rect_tuple(control) -> tuple[int, int, int, int]:
    rect = control.BoundingRectangle
    if rect is None:
        return (0, 0, 0, 0)
    return (int(rect.left), int(rect.top), int(rect.width), int(rect.height))


def _window_from_app(app, window_control=None) -> Window:
    ctrl = window_control if window_control is not None else app
    x, y, width, height = _rect_tuple(ctrl)
    name = (window_control.Title if window_control is not None else None) or app.Name or ""
    bundle_id = app.BundleIdentifier or ""
    return Window(
        name=name,
        status=_STATUS_MAP.get(app.Status, WindowStatus.Windowless),
        x=x,
        y=y,
        width=width,
        height=height,
        process_id=app.PID or 0,
        is_browser=bundle_id in BROWSER_BUNDLE_IDS,
        bundle_id=bundle_id,
    )


class Desktop(DesktopBase):
    """Controls the local macOS desktop through the Accessibility API."""

    def __init__(self) -> None:
        self._open = False
        self._tree = Tree()

    # -- Lifecycle ----------------------------------------------------------

    def open(self) -> None:
        if not ax.IsAccessibilityEnabledWithPrompt():
            raise PermissionError(
                "Accessibility permission is not granted. Enable it in "
                "System Settings > Privacy & Security > Accessibility."
            )
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    # -- State / inspection ---------------------------------------------------

    def get_state(self, as_bytes: bool = False) -> DesktopState:
        active_window = self.get_foreground_window()
        return DesktopState(
            active_window=active_window,
            windows=self.get_windows(),
            screenshot=self.get_screenshot(as_bytes=as_bytes),
            tree_state=self._tree.get_state(active_window),
        )

    def get_screen_size(self) -> Size:
        width, height = ax.GetScreenSize()
        return Size(width=int(width), height=int(height))

    def get_windows(self) -> list[Window]:
        windows: list[Window] = []
        for app in ax.GetRunningApplications(policy="Regular"):
            app_windows = app.Windows
            if not app_windows:
                windows.append(_window_from_app(app))
                continue
            for window_control in app_windows:
                windows.append(_window_from_app(app, window_control))
        return windows

    def get_foreground_window(self) -> Window | None:
        pid = ax.GetForegroundWindowPID()
        if pid is None:
            return None
        app_ctrl = ax.ApplicationControl(pid=pid)
        window_control = ax.GetForegroundControl()
        return _window_from_app(app_ctrl, window_control)

    def get_screenshot(self, as_bytes: bool = False):
        image = ax.CGImageToPIL(ax.CaptureScreen())
        if not as_bytes:
            return image
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    # -- Pointer ----------------------------------------------------------------

    def click(
        self,
        loc: tuple[int, int],
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
    ) -> None:
        x, y = loc
        if clicks <= 0:
            ax.MoveTo(x, y)
            return
        if button == "left" and clicks == 2:
            ax.DoubleClick(x, y)
            return
        click_fn = {"left": ax.Click, "right": ax.RightClick, "middle": ax.MiddleClick}[button]
        for _ in range(clicks):
            click_fn(x, y)

    def move(self, loc: tuple[int, int]) -> None:
        ax.MoveTo(*loc)

    def drag(self, loc: tuple[int, int]) -> None:
        start_x, start_y = ax.GetCursorPos()
        ax.DragTo(start_x, start_y, loc[0], loc[1])

    def scroll(
        self,
        loc: tuple[int, int] | None,
        orientation: Literal["vertical", "horizontal"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> None:
        if loc is not None:
            ax.MoveTo(*loc)
        wheel_fn = {"up": ax.WheelUp, "down": ax.WheelDown, "left": ax.WheelLeft, "right": ax.WheelRight}[direction]
        wheel_fn(wheel_times)

    # -- Keyboard -----------------------------------------------------------

    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool = False,
        press_enter: bool = False,
    ) -> None:
        ax.Click(*loc)
        if clear:
            ax.HotKey("command", "a")
            ax.HotKey("delete")
        elif caret_position == "start":
            ax.HotKey("home")
        elif caret_position == "end":
            ax.HotKey("end")
        ax.TypeText(text)
        if press_enter:
            ax.HotKey("return")

    def shortcut(self, shortcut: str) -> None:
        keys = [part.strip() for part in shortcut.replace("-", "+").split("+") if part.strip()]
        ax.HotKey(*keys)

    # -- Application management ---------------------------------------------

    def app(
        self,
        mode: Literal["launch", "switch", "resize", "move"] = "launch",
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ) -> str:
        if mode == "launch":
            assert name is not None
            if ax.LaunchApplication(name):
                return f"Launched {name}."
            return f"Failed to launch {name}."
        if mode == "switch":
            assert name is not None
            target = ax.GetRunningApplicationByName(name)
            if target is None or not target.Activate():
                return f"Could not find running application {name}."
            return f"Switched to {name}."
        if mode == "resize":
            assert size is not None
            window = ax.GetForegroundControl()
            if window is None or not window.Resize(size[0], size[1]):
                return "Could not resize the frontmost window."
            return f"Resized frontmost window to {size[0]}x{size[1]}."
        if mode == "move":
            assert loc is not None
            window = ax.GetForegroundControl()
            if window is None or not window.MoveWindowTo(loc[0], loc[1]):
                return "Could not move the frontmost window."
            return f"Moved frontmost window to {loc[0]},{loc[1]}."
        return f"Unknown app mode: {mode}"

    # -- Utility --------------------------------------------------------------

    def wait(self, duration: float) -> None:
        time.sleep(duration)
