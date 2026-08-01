"""CDP Ads Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..page.types import FrameId

class AdFrameData(TypedDict, total=True):
    """Ad frame data."""
    frameId: FrameId
    """The DevTools frame token."""
    networkBytes: float
    """The network bytes of the frame."""
    cpuTime: float
    """The CPU time of the frame, in milliseconds."""
    initialOrigin: NotRequired[str]
    """The initial origin of the frame. To minimize the payload size, this is only sent once per frame."""
class AdMetrics(TypedDict, total=True):
    """Ad metrics for a page."""
    viewportAdDensityByArea: int
    """The viewport ad density by area, represented as a percentage (an integer between 0 and 100)."""
    averageViewportAdDensityByArea: float
    """The time-weighted average of the viewport ad density by area, measured across the duration of the page."""
    viewportAdCount: int
    """The number of ads currently visible within the viewport."""
    averageViewportAdCount: float
    """The time-weighted average of the viewport ad count, measured across the duration of the page."""
    totalAdCpuTime: float
    """The total ad CPU usage, in milliseconds."""
    totalAdNetworkBytes: float
    """The total ad network bytes."""
    updateAdFrames: List[AdFrameData]
    """The list of ad frames that have been updated since the last event."""
    removeAdFrames: List[FrameId]
    """The list of ad frame IDs that have been removed since the last event."""