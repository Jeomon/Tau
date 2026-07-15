"""Platform-neutral desktop control types. Platform packages (macos, windows) each
provide a concrete Desktop subclass; computer_use/__init__.py selects the right one
at import time based on sys.platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL.Image import Image


@dataclass
class Size:
    """Screen or window dimensions in logical pixels."""

    width: int
    height: int

    def to_string(self) -> str:
        return f"({self.width},{self.height})"


class WindowStatus(StrEnum):
    """Normalized window visibility/focus state across platforms."""

    Active = "active"
    Fullscreen = "fullscreen"
    Visible = "visible"
    Minimized = "minimized"
    Hidden = "hidden"
    Windowless = "windowless"


@dataclass
class Window:
    """Platform-agnostic window descriptor."""

    name: str
    status: WindowStatus
    x: int
    y: int
    width: int
    height: int
    process_id: int
    is_browser: bool = False
    bundle_id: str = ""  # macOS: app bundle identifier
    native_handle: int = 0  # Windows: HWND


@dataclass
class DesktopState:
    """Snapshot returned by Desktop.get_state(). screenshot is None when
    use_screenshot=False was requested; tree_state is None when
    use_accessibility=False or the platform has no accessibility tree."""

    active_window: Window | None
    windows: list[Window] = field(default_factory=list)
    screenshot: Image | bytes | None = None
    tree_state: object | None = None

    def windows_to_string(self) -> str:
        if not self.windows:
            return "No open applications."
        return "\n".join(
            f"{w.name} [{w.status.value}] pid={w.process_id}" for w in self.windows
        )

    def active_window_to_string(self) -> str:
        if self.active_window is None:
            return "No focused window."
        w = self.active_window
        return f"{w.name} [{w.status.value}] pid={w.process_id}"

    def to_string(self) -> str:
        return (
            f"Active window: {self.active_window_to_string()}\n\n"
            f"Open windows:\n{self.windows_to_string()}"
        )


class Desktop(ABC):
    """Abstract desktop controller. Callers interact exclusively through this
    interface so higher-level logic stays platform-neutral."""

    # -- Lifecycle --------------------------------------------------------

    @abstractmethod
    def open(self) -> None:
        """Start the desktop session (check permissions, start watchdog if any)."""

    @abstractmethod
    def close(self) -> None:
        """Stop the desktop session and release resources."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """True when the session is active (after open() and before close())."""

    # -- State / inspection ------------------------------------------------

    @abstractmethod
    def get_state(
        self,
        as_bytes: bool = False,
        use_screenshot: bool = True,
        use_accessibility: bool = True,
    ) -> DesktopState:
        """Return a snapshot of the current desktop. Skips capturing the
        screenshot/accessibility tree when the corresponding flag is False,
        so callers that only need one can avoid paying for the other."""

    @abstractmethod
    def get_screen_size(self) -> Size:
        """Return the combined virtual screen size in logical pixels."""

    @abstractmethod
    def get_windows(self) -> list[Window]:
        """Return all currently visible/open windows."""

    @abstractmethod
    def get_foreground_window(self) -> Window | None:
        """Return the window that currently has keyboard focus, or None."""

    @abstractmethod
    def get_screenshot(self, as_bytes: bool = False) -> Image | bytes:
        """Capture the full screen."""

    # -- Pointer: click, move, scroll, drag --------------------------------

    @abstractmethod
    def click(
        self,
        loc: tuple[int, int],
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
    ) -> None:
        """Click at screen coordinate loc. clicks=0 only moves the pointer."""

    @abstractmethod
    def move(self, loc: tuple[int, int]) -> None:
        """Move the pointer to loc without clicking."""

    @abstractmethod
    def drag(self, loc: tuple[int, int]) -> None:
        """Drag from the current pointer position to loc."""

    @abstractmethod
    def scroll(
        self,
        loc: tuple[int, int] | None,
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> None:
        """Scroll at loc, or the current pointer position when loc is None."""

    # -- Keyboard: type, shortcut -------------------------------------------

    @abstractmethod
    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool = False,
        press_enter: bool = False,
    ) -> None:
        """Click loc then type text into the focused field."""

    @abstractmethod
    def shortcut(self, shortcut: str) -> None:
        """Press a keyboard shortcut such as 'command+c' or 'ctrl+z'."""

    # -- Application management ---------------------------------------------

    @abstractmethod
    def app(
        self,
        mode: Literal["launch", "switch", "resize", "move"] = "launch",
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ) -> str:
        """Launch, switch to, resize, or move an application window.

        name is required for launch/switch; size is required for resize;
        loc is required for move. Returns a human-readable status string.
        """

    # -- Utility --------------------------------------------------------------

    @abstractmethod
    def wait(self, duration: float) -> None:
        """Block for duration seconds."""
