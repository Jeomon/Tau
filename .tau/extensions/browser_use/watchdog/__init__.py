"""Browser watchdog framework and default monitors."""

from .about_blank import AboutBlankWatchdog
from .base import BaseWatchdog
from .crash import CrashWatchdog
from .downloads import DownloadsWatchdog
from .navigation import NavigationWatchdog
from .permissions import PermissionsWatchdog
from .popups import PopupsWatchdog
from .registry import WatchdogRegistry
from .security import SecurityWatchdog
from .storage import StorageStateWatchdog

DEFAULT_WATCHDOGS = (
    PermissionsWatchdog,
    CrashWatchdog,
    SecurityWatchdog,
    DownloadsWatchdog,
    StorageStateWatchdog,
    PopupsWatchdog,
    AboutBlankWatchdog,
)

__all__ = [
    "AboutBlankWatchdog",
    "BaseWatchdog",
    "CrashWatchdog",
    "DEFAULT_WATCHDOGS",
    "DownloadsWatchdog",
    "NavigationWatchdog",
    "PermissionsWatchdog",
    "PopupsWatchdog",
    "SecurityWatchdog",
    "StorageStateWatchdog",
    "WatchdogRegistry",
]
