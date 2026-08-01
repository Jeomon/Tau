"""CDP BackgroundService Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..types import BackgroundServiceEvent
    from ..types import ServiceName

class recordingStateChangedEvent(TypedDict, total=True):
    isRecording: bool
    service: ServiceName
class backgroundServiceEventReceivedEvent(TypedDict, total=True):
    backgroundServiceEvent: BackgroundServiceEvent