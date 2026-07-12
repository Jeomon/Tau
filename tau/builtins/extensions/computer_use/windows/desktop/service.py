"""Windows Desktop implementation built on the uia UI Automation bindings."""

from __future__ import annotations

import io
import subprocess
import time
from typing import Literal

from ...types import Desktop as DesktopBase
from ...types import DesktopState, Size, Window, WindowStatus
from .. import uia
from ..tree import Tree

_BROWSER_EXE_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "iexplore.exe",
    "vivaldi.exe",
}


def _window_status(window) -> WindowStatus:
    handle = window.NativeWindowHandle
    if not uia.IsWindowVisible(handle):
        return WindowStatus.Hidden
    if uia.IsIconic(handle):
        return WindowStatus.Minimized
    if uia.IsZoomed(handle) and window.NativeWindowHandle == uia.GetForegroundWindow():
        return WindowStatus.Fullscreen
    if handle == uia.GetForegroundWindow():
        return WindowStatus.Active
    return WindowStatus.Visible


def _process_exe_names() -> dict[int, str]:
    return {p.pid: p.exeName.lower() for p in uia.GetProcesses(detailedInfo=False)}


def _window_from_control(control, exe_names: dict[int, str] | None = None) -> Window:
    rect = control.BoundingRectangle
    x, y, width, height = (
        (int(rect.left), int(rect.top), int(rect.width()), int(rect.height())) if rect else (0, 0, 0, 0)
    )
    pid = control.ProcessId or 0
    exe_name = (exe_names or {}).get(pid, "")
    return Window(
        name=control.Name or "",
        status=_window_status(control),
        x=x,
        y=y,
        width=width,
        height=height,
        process_id=pid,
        is_browser=exe_name in _BROWSER_EXE_NAMES,
        native_handle=control.NativeWindowHandle or 0,
    )


class Desktop(DesktopBase):
    """Controls the local Windows desktop through UI Automation."""

    def __init__(self) -> None:
        self._open = False
        self._tree = Tree(self)

    def is_window_browser(self, node) -> bool:
        """True when node belongs to a known browser process."""
        exe_name = _process_exe_names().get(node.ProcessId or 0, "")
        return exe_name in _BROWSER_EXE_NAMES

    # -- Lifecycle ----------------------------------------------------------

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    # -- State / inspection ---------------------------------------------------

    def get_state(self, as_bytes: bool = False) -> DesktopState:
        active_window = self.get_foreground_window()
        active_handle = active_window.native_handle if active_window else None
        other_handles = [
            control.NativeWindowHandle
            for control in uia.GetRootControl().GetChildren()
            if control.NativeWindowHandle and control.NativeWindowHandle != active_handle
        ]
        return DesktopState(
            active_window=active_window,
            windows=self.get_windows(),
            screenshot=self.get_screenshot(as_bytes=as_bytes),
            tree_state=self._tree.get_state(active_handle, other_handles),
        )

    def get_screen_size(self) -> Size:
        width, height = uia.GetScreenSize()
        return Size(width=int(width), height=int(height))

    def get_windows(self) -> list[Window]:
        exe_names = _process_exe_names()
        windows: list[Window] = []
        for control in uia.GetRootControl().GetChildren():
            if not isinstance(control, uia.WindowControl):
                continue
            if not uia.IsWindowVisible(control.NativeWindowHandle):
                continue
            windows.append(_window_from_control(control, exe_names))
        return windows

    def get_foreground_window(self) -> Window | None:
        control = uia.GetForegroundControl()
        if control is None:
            return None
        return _window_from_control(control, _process_exe_names())

    def get_screenshot(self, as_bytes: bool = False):
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
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
            uia.MoveTo(x, y)
            return
        click_fn = {"left": uia.Click, "right": uia.RightClick, "middle": uia.MiddleClick}[button]
        for _ in range(clicks):
            click_fn(x, y)

    def move(self, loc: tuple[int, int]) -> None:
        uia.MoveTo(*loc)

    def drag(self, loc: tuple[int, int]) -> None:
        start_x, start_y = uia.GetCursorPos()
        uia.DragDrop(start_x, start_y, loc[0], loc[1])

    def scroll(
        self,
        loc: tuple[int, int] | None,
        orientation: Literal["vertical", "horizontal"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> None:
        if loc is not None:
            uia.MoveTo(*loc)
        wheel_fn = uia.WheelUp if direction in ("up", "left") else uia.WheelDown
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
        uia.Click(*loc)
        if clear:
            uia.SendKeys("{Ctrl}a{Delete}")
        elif caret_position == "start":
            uia.SendKeys("{Home}")
        elif caret_position == "end":
            uia.SendKeys("{End}")
        uia.SendKeys(text)
        if press_enter:
            uia.SendKeys("{Enter}")

    def shortcut(self, shortcut: str) -> None:
        parts = [part.strip() for part in shortcut.replace("-", "+").split("+") if part.strip()]
        modifiers = {"ctrl", "control", "alt", "shift", "win", "windows", "cmd", "command"}
        keys = "".join(
            f"{{{part.capitalize()}}}" if part.lower() in modifiers else part
            for part in parts
        )
        uia.SendKeys(keys)

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
            try:
                subprocess.Popen(name, shell=True)
            except OSError as exc:
                return f"Failed to launch {name}: {exc}"
            return f"Launched {name}."
        if mode == "switch":
            assert name is not None
            control = uia.WindowControl(searchDepth=1, Name=name)
            if not control.Exists(maxSearchSeconds=1) or not control.SetActive():
                return f"Could not find running application {name}."
            return f"Switched to {name}."
        if mode == "resize":
            assert size is not None
            control = uia.GetForegroundControl()
            if control is None:
                return "Could not resize the frontmost window."
            rect = control.BoundingRectangle
            uia.MoveWindow(control.NativeWindowHandle, rect.left, rect.top, size[0], size[1])
            return f"Resized frontmost window to {size[0]}x{size[1]}."
        if mode == "move":
            assert loc is not None
            control = uia.GetForegroundControl()
            if control is None:
                return "Could not move the frontmost window."
            rect = control.BoundingRectangle
            uia.MoveWindow(control.NativeWindowHandle, loc[0], loc[1], rect.width(), rect.height())
            return f"Moved frontmost window to {loc[0]},{loc[1]}."
        return f"Unknown app mode: {mode}"

    # -- Utility --------------------------------------------------------------

    def wait(self, duration: float) -> None:
        time.sleep(duration)
