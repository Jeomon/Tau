"""CDP WebAudio Methods Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..types import ContextRealtimeData
    from ..types import GraphObjectId



class getRealtimeDataParameters(TypedDict, total=True):
    contextId: GraphObjectId


class getRealtimeDataReturns(TypedDict):
    realtimeData: ContextRealtimeData