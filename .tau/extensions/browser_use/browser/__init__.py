"""Local Chromium management and remote CDP connections."""

from hooks.service import HookHandler, Hooks, Unsubscribe

from .hooks import *
from .hooks import __all__ as _hook_exports
from .page import Page
from .download import Download, DownloadRegistry, DownloadState
from .network import InterceptDecision, Network, Request, RequestInterceptor
from .service import Browser
from .session import Session, Target
from .settings import BrowserSettings
from .state import (
    AccessibilityNode,
    Bounds,
    DOMDiff,
    DOMTreeNode,
    IframeContentHint,
    PaginationButton,
    SemanticNode,
    ElementState,
    PageState,
    ViewportState,
)
from .storage import OriginStorage, Storage, StorageState, StorageValue
from .types import (
    BrowserError,
    BrowserExecutableNotFound,
    BrowserLaunchError,
    BrowserState,
    BrowserStatus,
    DownloadCancelledError,
    DownloadError,
    DownloadPathError,
    DownloadTimeoutError,
    LoadState,
    NavigationError,
    NavigationBlockedError,
    NavigationTimeoutError,
    StaleElementError,
    SessionID,
    TargetID,
    WaitTimeoutError,
)

__all__ = [
    "Browser",
    "BrowserError",
    "BrowserExecutableNotFound",
    "BrowserLaunchError",
    "BrowserSettings",
    "BrowserState",
    "BrowserStatus",
    "Bounds",
    "DOMDiff",
    "DOMTreeNode",
    "IframeContentHint",
    "PaginationButton",
    "SemanticNode",
    "ElementState",
    "Download",
    "DownloadRegistry",
    "DownloadState",
    "DownloadError",
    "DownloadCancelledError",
    "DownloadPathError",
    "DownloadTimeoutError",
    "HookHandler",
    "Hooks",
    "LoadState",
    "NavigationError",
    "NavigationBlockedError",
    "NavigationTimeoutError",
    "Network",
    "Page",
    "PageState",
    "Request",
    "RequestInterceptor",
    "InterceptDecision",
    "SessionID",
    "Session",
    "Storage",
    "StorageState",
    "StorageValue",
    "OriginStorage",
    "TargetID",
    "Target",
    "StaleElementError",
    "WaitTimeoutError",
    "Unsubscribe",
    "AccessibilityNode",
    "ViewportState",
    *_hook_exports,
]
