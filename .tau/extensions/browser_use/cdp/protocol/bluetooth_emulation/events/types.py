"""CDP BluetoothEmulation Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..types import CharacteristicOperationType
    from ..types import CharacteristicWriteType
    from ..types import DescriptorOperationType
    from ..types import GATTOperationType

class gattOperationReceivedEvent(TypedDict, total=True):
    address: str
    type: GATTOperationType
class characteristicOperationReceivedEvent(TypedDict, total=True):
    characteristicId: str
    type: CharacteristicOperationType
    data: NotRequired[str]
    writeType: NotRequired[CharacteristicWriteType]
class descriptorOperationReceivedEvent(TypedDict, total=True):
    descriptorId: str
    type: DescriptorOperationType
    data: NotRequired[str]