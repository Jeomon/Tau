"""Types exposed by the local browser service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias

TargetID: TypeAlias = str
SessionID: TypeAlias = str
LoadState: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]


class BrowserStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class BrowserState:
    status: BrowserStatus = BrowserStatus.STOPPED
    pid: int | None = None
    cdp_url: str | None = None
    user_data_dir: Path | None = None
    remote: bool = False


class BrowserError(RuntimeError):
    """Base error raised by the local browser service."""


class BrowserExecutableNotFound(BrowserError):
    """Raised when no supported Chromium executable can be found."""


class BrowserLaunchError(BrowserError):
    """Raised when the local browser cannot be started or connected."""


class NavigationError(BrowserError):
    """Raised when a page navigation fails."""


class NavigationTimeoutError(NavigationError):
    """Raised when a page does not reach the requested load state in time."""


class NavigationBlockedError(NavigationError):
    """Raised when navigation violates the configured security policy."""


class WaitTimeoutError(BrowserError):
    """Raised when a page condition is not satisfied before its timeout."""


class StaleElementError(BrowserError):
    """Raised when a captured DOM element is no longer attached."""


class DownloadError(BrowserError):
    """Base error for download operations."""


class DownloadTimeoutError(DownloadError):
    """Raised when a download does not start or finish before its timeout."""


class DownloadCancelledError(DownloadError):
    """Raised when Chrome reports that a download was canceled."""


class DownloadPathError(DownloadError):
    """Raised when Chrome reports a download outside the configured directory."""
