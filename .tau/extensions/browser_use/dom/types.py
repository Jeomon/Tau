"""DOM state types used by browser inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Bounds:
    """Element rectangle in CSS viewport coordinates."""

    x: float
    y: float
    width: float
    height: float
    document_x: float
    document_y: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class AccessibilityNode:
    backend_node_id: int
    role: str = ""
    name: str = ""
    description: str = ""
    value: Any = None
    disabled: bool = False
    focused: bool = False
    hidden: bool = False
    focusable: bool = False
    editable: bool = False
    checked: Any = None
    expanded: bool | None = None
    pressed: Any = None
    selected: bool | None = None
    required: bool = False
    haspopup: str = ""
    autocomplete: str = ""
    keyshortcuts: str = ""


@dataclass(frozen=True, slots=True)
class Element:
    backend_node_id: int
    tag_name: str
    text: str
    attributes: dict[str, str]
    bounds: Bounds
    clickable: bool
    scrollable: bool
    depth: int
    parent_backend_node_id: int | None = None
    accessibility: AccessibilityNode | None = None
    frame_id: str | None = None
    session_id: str | None = None
    frame_offset_x: float = 0
    frame_offset_y: float = 0
    has_content_document: bool = False
    element_id: str = ""
    structural_path: str = ""
    shadow_root_type: str | None = None
    is_shadow_host: bool = False
    paint_order: int = 0
    stable_hash: str = ""
    strongly_interactive: bool = False

    @property
    def visible(self) -> bool:
        """Elements returned by the DOM service have passed visibility filtering."""
        return True

    @property
    def semantic_key(self) -> tuple[str, ...]:
        identity = (
            self.attributes.get("id")
            or self.attributes.get("data-testid")
            or self.attributes.get("name")
            or self.attributes.get("aria-label")
            or ""
        )
        return (
            self.frame_id or "",
            self.tag_name,
            identity,
            self.text.strip(),
        )


@dataclass(frozen=True, slots=True)
class DOMDiff:
    added: tuple[str, ...] = field(default_factory=tuple)
    removed: tuple[str, ...] = field(default_factory=tuple)
    changed: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DOMTreeNode:
    element: Element
    children: tuple["DOMTreeNode", ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SemanticNode:
    node_type: int
    node_name: str
    text: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    backend_node_id: int | None = None
    children: tuple["SemanticNode", ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IframeContentHint:
    frame_id: str | None
    hidden_interactive_count: int
    has_hidden_content: bool


@dataclass(frozen=True, slots=True)
class PaginationButton:
    element_id: str
    direction: str
    text: str
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class DOMState:
    elements: tuple[Element, ...] = field(default_factory=tuple)
    accessibility: tuple[AccessibilityNode, ...] = field(default_factory=tuple)
    snapshot: dict[str, Any] | None = None
    diff: DOMDiff = field(default_factory=DOMDiff)
    roots: tuple[DOMTreeNode, ...] = field(default_factory=tuple)
    semantic_roots: tuple[SemanticNode, ...] = field(default_factory=tuple)
    iframe_hints: tuple[IframeContentHint, ...] = field(default_factory=tuple)
    pagination_buttons: tuple[PaginationButton, ...] = field(default_factory=tuple)
