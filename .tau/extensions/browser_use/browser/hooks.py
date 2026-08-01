"""Typed events emitted by the browser service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .settings import BrowserSettings
from .types import BrowserState, SessionID, TargetID


@dataclass(frozen=True, slots=True)
class BrowserStartEvent:
    type: Literal["browser_start"] = field(default="browser_start", init=False)
    settings: BrowserSettings = field(default_factory=BrowserSettings)


@dataclass(frozen=True, slots=True)
class BrowserStartedEvent:
    type: Literal["browser_started"] = field(default="browser_started", init=False)
    state: BrowserState = field(default_factory=BrowserState)


@dataclass(frozen=True, slots=True)
class BrowserStartFailedEvent:
    type: Literal["browser_start_failed"] = field(
        default="browser_start_failed", init=False
    )
    error: BaseException = field(default_factory=RuntimeError)


@dataclass(frozen=True, slots=True)
class BrowserStopEvent:
    type: Literal["browser_stop"] = field(default="browser_stop", init=False)
    state: BrowserState = field(default_factory=BrowserState)


@dataclass(frozen=True, slots=True)
class BrowserStoppedEvent:
    type: Literal["browser_stopped"] = field(default="browser_stopped", init=False)
    state: BrowserState = field(default_factory=BrowserState)


@dataclass(frozen=True, slots=True)
class PageCreatedEvent:
    type: Literal["page_created"] = field(default="page_created", init=False)
    target_id: TargetID = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class PageClosedEvent:
    type: Literal["page_closed"] = field(default="page_closed", init=False)
    target_id: TargetID = ""
    success: bool = False


@dataclass(frozen=True, slots=True)
class PageAttachedEvent:
    type: Literal["page_attached"] = field(default="page_attached", init=False)
    target_id: TargetID = ""
    session_id: SessionID = ""


@dataclass(frozen=True, slots=True)
class ElementSelectedEvent:
    type: Literal["element_selected"] = field(default="element_selected", init=False)
    node: Any = None


@dataclass(frozen=True, slots=True)
class NavigateToUrlEvent:
    type: Literal["navigate_to_url"] = field(default="navigate_to_url", init=False)
    url: str = ""
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout_ms: int | None = None
    new_tab: bool = False


@dataclass(frozen=True, slots=True)
class ClickElementEvent:
    type: Literal["click_element"] = field(default="click_element", init=False)
    node: Any = None
    button: Literal["left", "right", "middle"] = "left"


@dataclass(frozen=True, slots=True)
class ClickCoordinateEvent:
    type: Literal["click_coordinate"] = field(default="click_coordinate", init=False)
    coordinate_x: int = 0
    coordinate_y: int = 0
    button: Literal["left", "right", "middle"] = "left"
    force: bool = False


@dataclass(frozen=True, slots=True)
class HoverEvent:
    type: Literal["hover"] = field(default="hover", init=False)
    coordinate_x: int = 0
    coordinate_y: int = 0


@dataclass(frozen=True, slots=True)
class DragEvent:
    type: Literal["drag"] = field(default="drag", init=False)
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0
    button: Literal["left", "right", "middle"] = "left"


@dataclass(frozen=True, slots=True)
class TypeTextEvent:
    type: Literal["type_text"] = field(default="type_text", init=False)
    node: Any = None
    text: str = ""
    clear: bool = True
    is_sensitive: bool = False
    sensitive_key_name: str | None = None


@dataclass(frozen=True, slots=True)
class ScrollEvent:
    type: Literal["scroll"] = field(default="scroll", init=False)
    direction: Literal["up", "down", "left", "right"] = "down"
    amount: int = 0
    node: Any = None


@dataclass(frozen=True, slots=True)
class SwitchTabEvent:
    type: Literal["switch_tab"] = field(default="switch_tab", init=False)
    target_id: TargetID | None = None


@dataclass(frozen=True, slots=True)
class CloseTabEvent:
    type: Literal["close_tab"] = field(default="close_tab", init=False)
    target_id: TargetID = ""


@dataclass(frozen=True, slots=True)
class ScreenshotEvent:
    type: Literal["screenshot"] = field(default="screenshot", init=False)
    full_page: bool = False
    clip: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class BrowserStateRequestEvent:
    type: Literal["browser_state_request"] = field(
        default="browser_state_request", init=False
    )
    include_dom: bool = True
    include_screenshot: bool = True
    include_recent_events: bool = False


@dataclass(frozen=True, slots=True)
class GoBackEvent:
    type: Literal["go_back"] = field(default="go_back", init=False)


@dataclass(frozen=True, slots=True)
class GoForwardEvent:
    type: Literal["go_forward"] = field(default="go_forward", init=False)


@dataclass(frozen=True, slots=True)
class RefreshEvent:
    type: Literal["refresh"] = field(default="refresh", init=False)


@dataclass(frozen=True, slots=True)
class WaitEvent:
    type: Literal["wait"] = field(default="wait", init=False)
    seconds: float = 3.0
    max_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class SendKeysEvent:
    type: Literal["send_keys"] = field(default="send_keys", init=False)
    keys: str = ""


@dataclass(frozen=True, slots=True)
class KeyDownEvent:
    type: Literal["key_down"] = field(default="key_down", init=False)
    key: str = ""


@dataclass(frozen=True, slots=True)
class KeyUpEvent:
    type: Literal["key_up"] = field(default="key_up", init=False)
    key: str = ""


@dataclass(frozen=True, slots=True)
class UploadFileEvent:
    type: Literal["upload_file"] = field(default="upload_file", init=False)
    node: Any = None
    file_path: str = ""


@dataclass(frozen=True, slots=True)
class GetDropdownOptionsEvent:
    type: Literal["get_dropdown_options"] = field(
        default="get_dropdown_options", init=False
    )
    node: Any = None


@dataclass(frozen=True, slots=True)
class SelectDropdownOptionEvent:
    type: Literal["select_dropdown_option"] = field(
        default="select_dropdown_option", init=False
    )
    node: Any = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class ScrollToTextEvent:
    type: Literal["scroll_to_text"] = field(default="scroll_to_text", init=False)
    text: str = ""
    direction: Literal["up", "down"] = "down"


@dataclass(frozen=True, slots=True)
class BrowserLaunchEvent:
    type: Literal["browser_launch"] = field(default="browser_launch", init=False)


@dataclass(frozen=True, slots=True)
class BrowserKillEvent:
    type: Literal["browser_kill"] = field(default="browser_kill", init=False)


@dataclass(frozen=True, slots=True)
class BrowserConnectedEvent:
    type: Literal["browser_connected"] = field(default="browser_connected", init=False)
    cdp_url: str = ""


@dataclass(frozen=True, slots=True)
class TabCreatedEvent:
    type: Literal["tab_created"] = field(default="tab_created", init=False)
    target_id: TargetID = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class TabClosedEvent:
    type: Literal["tab_closed"] = field(default="tab_closed", init=False)
    target_id: TargetID = ""


@dataclass(frozen=True, slots=True)
class AgentFocusChangedEvent:
    type: Literal["agent_focus_changed"] = field(
        default="agent_focus_changed", init=False
    )
    target_id: TargetID = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class TargetCrashedEvent:
    type: Literal["target_crashed"] = field(default="target_crashed", init=False)
    target_id: TargetID = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class NavigationStartedEvent:
    type: Literal["navigation_started"] = field(
        default="navigation_started", init=False
    )
    target_id: TargetID = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class NavigationCompleteEvent:
    type: Literal["navigation_complete"] = field(
        default="navigation_complete", init=False
    )
    target_id: TargetID = ""
    url: str = ""
    status: int | None = None
    error_message: str | None = None
    loading_status: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserErrorEvent:
    type: Literal["browser_error"] = field(default="browser_error", init=False)
    error_type: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrowserReconnectingEvent:
    type: Literal["browser_reconnecting"] = field(
        default="browser_reconnecting", init=False
    )
    cdp_url: str = ""
    attempt: int = 0
    max_attempts: int = 0


@dataclass(frozen=True, slots=True)
class BrowserReconnectedEvent:
    type: Literal["browser_reconnected"] = field(
        default="browser_reconnected", init=False
    )
    cdp_url: str = ""
    attempt: int = 0
    downtime_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class SaveStorageStateEvent:
    type: Literal["save_storage_state"] = field(
        default="save_storage_state", init=False
    )
    path: str | None = None


@dataclass(frozen=True, slots=True)
class StorageStateSavedEvent:
    type: Literal["storage_state_saved"] = field(
        default="storage_state_saved", init=False
    )
    path: str = ""
    cookies_count: int = 0
    origins_count: int = 0


@dataclass(frozen=True, slots=True)
class LoadStorageStateEvent:
    type: Literal["load_storage_state"] = field(
        default="load_storage_state", init=False
    )
    path: str | None = None


@dataclass(frozen=True, slots=True)
class StorageStateLoadedEvent:
    type: Literal["storage_state_loaded"] = field(
        default="storage_state_loaded", init=False
    )
    path: str = ""
    cookies_count: int = 0
    origins_count: int = 0


@dataclass(frozen=True, slots=True)
class DownloadStartedEvent:
    type: Literal["download_started"] = field(default="download_started", init=False)
    guid: str = ""
    url: str = ""
    suggested_filename: str = ""
    auto_download: bool = False
    target_id: TargetID | None = None


@dataclass(frozen=True, slots=True)
class DownloadProgressEvent:
    type: Literal["download_progress"] = field(default="download_progress", init=False)
    guid: str = ""
    received_bytes: int = 0
    total_bytes: int = 0
    state: Literal["inProgress", "completed", "canceled"] = "inProgress"


@dataclass(frozen=True, slots=True)
class FileDownloadedEvent:
    type: Literal["file_downloaded"] = field(default="file_downloaded", init=False)
    guid: str | None = None
    url: str = ""
    path: str = ""
    file_name: str = ""
    file_size: int = 0
    file_type: str | None = None
    mime_type: str | None = None
    from_cache: bool = False
    auto_download: bool = False


BrowserEvent: TypeAlias = (
    BrowserStartEvent
    | BrowserStartedEvent
    | BrowserStartFailedEvent
    | BrowserStopEvent
    | BrowserStoppedEvent
    | PageCreatedEvent
    | PageClosedEvent
    | PageAttachedEvent
    | ElementSelectedEvent
    | NavigateToUrlEvent
    | ClickElementEvent
    | ClickCoordinateEvent
    | HoverEvent
    | DragEvent
    | TypeTextEvent
    | ScrollEvent
    | SwitchTabEvent
    | CloseTabEvent
    | ScreenshotEvent
    | BrowserStateRequestEvent
    | GoBackEvent
    | GoForwardEvent
    | RefreshEvent
    | WaitEvent
    | SendKeysEvent
    | KeyDownEvent
    | KeyUpEvent
    | UploadFileEvent
    | GetDropdownOptionsEvent
    | SelectDropdownOptionEvent
    | ScrollToTextEvent
    | BrowserLaunchEvent
    | BrowserKillEvent
    | BrowserConnectedEvent
    | TabCreatedEvent
    | TabClosedEvent
    | AgentFocusChangedEvent
    | TargetCrashedEvent
    | NavigationStartedEvent
    | NavigationCompleteEvent
    | BrowserErrorEvent
    | BrowserReconnectingEvent
    | BrowserReconnectedEvent
    | SaveStorageStateEvent
    | StorageStateSavedEvent
    | LoadStorageStateEvent
    | StorageStateLoadedEvent
    | DownloadStartedEvent
    | DownloadProgressEvent
    | FileDownloadedEvent
)

__all__ = [
    "AgentFocusChangedEvent",
    "BrowserConnectedEvent",
    "BrowserErrorEvent",
    "BrowserEvent",
    "BrowserKillEvent",
    "BrowserLaunchEvent",
    "BrowserReconnectedEvent",
    "BrowserReconnectingEvent",
    "BrowserStartEvent",
    "BrowserStartedEvent",
    "BrowserStartFailedEvent",
    "BrowserStateRequestEvent",
    "BrowserStopEvent",
    "BrowserStoppedEvent",
    "ClickCoordinateEvent",
    "ClickElementEvent",
    "CloseTabEvent",
    "DownloadProgressEvent",
    "DownloadStartedEvent",
    "DragEvent",
    "ElementSelectedEvent",
    "FileDownloadedEvent",
    "GetDropdownOptionsEvent",
    "GoBackEvent",
    "GoForwardEvent",
    "HoverEvent",
    "KeyDownEvent",
    "KeyUpEvent",
    "LoadStorageStateEvent",
    "NavigateToUrlEvent",
    "NavigationCompleteEvent",
    "NavigationStartedEvent",
    "PageAttachedEvent",
    "PageClosedEvent",
    "PageCreatedEvent",
    "RefreshEvent",
    "SaveStorageStateEvent",
    "ScreenshotEvent",
    "ScrollEvent",
    "ScrollToTextEvent",
    "SelectDropdownOptionEvent",
    "SendKeysEvent",
    "StorageStateLoadedEvent",
    "StorageStateSavedEvent",
    "SwitchTabEvent",
    "TabClosedEvent",
    "TabCreatedEvent",
    "TargetCrashedEvent",
    "TypeTextEvent",
    "UploadFileEvent",
    "WaitEvent",
]
