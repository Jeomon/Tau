"""Node and state dataclasses for the accessibility-tree snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

WARNING_MESSAGE = "The desktop UI services are temporarily unavailable. Please wait a few seconds and continue."
EMPTY_MESSAGE = "No elements found"

if TYPE_CHECKING:
    from ..ax.core import Rect


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
            left=int(rect.left),
            top=int(rect.top),
            right=int(rect.right),
            bottom=int(rect.bottom),
            width=int(rect.width),
            height=int(rect.height),
        )

    def get_center(self) -> Center:
        return Center(x=self.left + self.width // 2, y=self.top + self.height // 2)

    def xywh_to_string(self) -> str:
        return f"({self.left},{self.top},{self.width},{self.height})"

    def contains(self, other: BoundingBox) -> bool:
        return (
            self.left <= other.left
            and self.right >= other.right
            and self.top <= other.top
            and self.bottom >= other.bottom
        )


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
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrollElementNode:
    name: str
    control_type: str
    window_name: str
    bounding_box: BoundingBox
    center: Center
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextElementNode:
    text: str


ElementNode = TreeElementNode | ScrollElementNode | TextElementNode


@dataclass
class TreeState:
    status: bool = True
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
