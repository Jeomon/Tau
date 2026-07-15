"""Node and state dataclasses for the accessibility-tree snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

WARNING_MESSAGE = "The desktop UI services are temporarily unavailable. Please wait a few seconds and continue."
EMPTY_MESSAGE = "No elements found"

if TYPE_CHECKING:
    from ..uia.controls import Control
    from ..uia.core import Rect


@dataclass
class BoundingBox:
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int

    @classmethod
    def from_bounding_rectangle(cls, rect: Rect) -> BoundingBox:
        return cls(
            left=rect.left,
            top=rect.top,
            right=rect.right,
            bottom=rect.bottom,
            width=rect.width(),
            height=rect.height(),
        )

    def get_center(self) -> Center:
        return Center(x=self.left + self.width // 2, y=self.top + self.height // 2)

    def xywh_to_string(self) -> str:
        return f"({self.left},{self.top},{self.width},{self.height})"


@dataclass
class Center:
    x: int
    y: int

    def to_string(self) -> str:
        return f"({self.x},{self.y})"


@dataclass
class TreeElementNode:
    bounding_box: BoundingBox
    center: Center
    name: str = ""
    control_type: str = ""
    window_name: str = ""
    hwnd: int = 0
    control: Control | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrollElementNode:
    name: str
    control_type: str
    window_name: str
    bounding_box: BoundingBox
    center: Center
    hwnd: int = 0
    control: Control | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextElementNode:
    text: str


ElementNode = TreeElementNode | ScrollElementNode | TextElementNode
_SelectorNode = TreeElementNode | ScrollElementNode


class SelectorMap(dict):
    """Maps element index (as shown in the tree output) to its node.

        # id|window|control_type|name|coords|metadata
        0|Notepad|ButtonControl|Save|(640,400)|{}
        1|Notepad|EditControl|Search|(200,100)|{}

    Usage:
        selector = tree_state.build_selector_map()
        node = selector[0]
        control = selector.control_of(0)
        hwnd = selector.hwnd_of(0)
    """

    def node_of(self, index: int) -> _SelectorNode | None:
        return self.get(index)

    def control_of(self, index: int) -> Control | None:
        node = self.node_of(index)
        return node.control if node else None

    def hwnd_of(self, index: int) -> int | None:
        node = self.node_of(index)
        return node.hwnd if node else None

    def __repr__(self) -> str:
        return f"SelectorMap({len(self)} elements)"


@dataclass
class TreeState:
    status: bool = True
    root_node: TreeElementNode | None = None
    dom_node: ScrollElementNode | None = None
    interactive_nodes: list[TreeElementNode] = field(default_factory=list)
    scrollable_nodes: list[ScrollElementNode] = field(default_factory=list)
    dom_informative_nodes: list[TextElementNode] = field(default_factory=list)

    def interactive_elements_to_string(self) -> str:
        if not self.status:
            return WARNING_MESSAGE
        if not self.interactive_nodes:
            return EMPTY_MESSAGE
        header = "# id|window|control_type|name|coords|metadata"
        rows = [header]
        for idx, node in enumerate(self.interactive_nodes):
            rows.append(
                f"{idx}|{node.window_name}|{node.control_type}|{node.name}|"
                f"{node.center.to_string()}|{json.dumps(node.metadata)}"
            )
        return "\n".join(rows)

    def scrollable_elements_to_string(self) -> str:
        if not self.status:
            return WARNING_MESSAGE
        if not self.scrollable_nodes:
            return EMPTY_MESSAGE
        header = "# id|window|control_type|name|coords|metadata"
        rows = [header]
        base_index = len(self.interactive_nodes)
        for idx, node in enumerate(self.scrollable_nodes):
            rows.append(
                f"{base_index + idx}|{node.window_name}|{node.control_type}|{node.name}|"
                f"{node.center.to_string()}|{json.dumps(node.metadata)}"
            )
        return "\n".join(rows)

    def build_selector_map(self) -> SelectorMap:
        nodes: list[_SelectorNode] = list(self.interactive_nodes) + list(self.scrollable_nodes)
        return SelectorMap(enumerate(nodes))
