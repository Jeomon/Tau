"""Typed page-level state composed from browser and DOM services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..dom.types import (
    AccessibilityNode,
    Bounds,
    DOMDiff,
    DOMTreeNode,
    Element,
    IframeContentHint,
    PaginationButton,
    SemanticNode,
)

_INCLUDE_ATTRS = (
    "aria-label",
    "checked",
    "disabled",
    "href",
    "name",
    "placeholder",
    "role",
    "selected",
    "title",
    "type",
    "value",
)


def _build_attrs(element: Element) -> str:
    parts: list[str] = []
    attrs = element.attributes

    for key in _INCLUDE_ATTRS:
        val = attrs.get(key, "").strip()
        if val:
            parts.append(f"{key}={val[:80]}")

    if element.accessibility:
        ax = element.accessibility
        if ax.role and "role" not in attrs:
            parts.append(f"role={ax.role}")
        if (
            ax.name
            and ax.name.strip()
            and ax.name.strip() != element.text.strip()
            and "aria-label" not in attrs
        ):
            parts.append(f"aria-label={ax.name.strip()[:80]}")

    return " ".join(parts)


def _render_element(element: Element, new_ids: set[str]) -> str:
    indent = "\t" * element.depth
    attr_str = _build_attrs(element)
    if attr_str:
        attr_str = f" {attr_str}"

    shadow = ""
    if element.shadow_root_type:
        kind = "closed" if element.shadow_root_type.lower() == "closed" else "open"
        shadow = f"|shadow({kind})|"

    text = element.text.strip()
    tag = element.tag_name

    if element.clickable and element.element_id:
        marker = "*" if element.element_id in new_ids else ""
        if text:
            return f"{indent}{shadow}{marker}[{element.element_id}]<{tag}{attr_str}>{text}</{tag}>"
        return f"{indent}{shadow}{marker}[{element.element_id}]<{tag}{attr_str} />"

    if element.scrollable:
        if text:
            return f"{indent}{shadow}|scroll element|<{tag}{attr_str}>{text}</{tag}>"
        return f"{indent}{shadow}|scroll element|<{tag}{attr_str}>"

    if text:
        return f"{indent}{shadow}<{tag}{attr_str}>{text}</{tag}>"
    return f"{indent}{shadow}<{tag}{attr_str}>"


ElementState = Element


@dataclass(frozen=True, slots=True)
class ViewportState:
    width: float
    height: float
    page_x: float
    page_y: float
    scale: float
    device_pixel_ratio: float


@dataclass(frozen=True, slots=True)
class PageState:
    target_id: str
    url: str
    title: str
    viewport: ViewportState
    elements: tuple[Element, ...] = field(default_factory=tuple)
    accessibility: tuple[AccessibilityNode, ...] = field(default_factory=tuple)
    screenshot: str | None = None
    dom_snapshot: dict[str, Any] | None = None
    dom_diff: DOMDiff = field(default_factory=DOMDiff)
    dom_roots: tuple[DOMTreeNode, ...] = field(default_factory=tuple)
    semantic_roots: tuple[SemanticNode, ...] = field(default_factory=tuple)
    iframe_hints: tuple[IframeContentHint, ...] = field(default_factory=tuple)
    pagination_buttons: tuple[PaginationButton, ...] = field(default_factory=tuple)

    def screenshot_to_css(self, x: float, y: float) -> tuple[float, float]:
        ratio = self.viewport.device_pixel_ratio * self.viewport.scale
        return x / ratio, y / ratio

    def css_to_screenshot(self, x: float, y: float) -> tuple[float, float]:
        ratio = self.viewport.device_pixel_ratio * self.viewport.scale
        return x * ratio, y * ratio

    def to_text(self) -> str:
        lines: list[str] = []

        lines.append(f"URL: {self.url} | Title: {self.title}")

        interactive = sum(1 for e in self.elements if e.clickable)
        scrollable_only = sum(1 for e in self.elements if e.scrollable and not e.clickable)
        iframes = sum(1 for e in self.elements if e.tag_name.lower() in ("iframe", "frame"))
        stat_parts = [f"{interactive} interactive"]
        if scrollable_only:
            stat_parts.append(f"{scrollable_only} scrollable")
        if iframes:
            stat_parts.append(f"{iframes} iframes")
        lines.append(f"<page_stats>{', '.join(stat_parts)}</page_stats>")

        if self.viewport.height > 0:
            pages_above = self.viewport.page_y / self.viewport.height
            lines.append(f"<page_info>{pages_above:.1f} pages above viewport</page_info>")

        new_ids: set[str] = set(self.dom_diff.added)

        lines.append("")
        if self.elements:
            lines.append("[Start of page]")
            for element in self.elements:
                lines.append(_render_element(element, new_ids))
            lines.append("[End of page]")
        else:
            lines.append("empty page")

        for hint in self.iframe_hints:
            if hint.has_hidden_content:
                lines.append(
                    f"... ({hint.hidden_interactive_count} interactive elements"
                    f" hidden in iframe {hint.frame_id or 'unknown'} — scroll to reveal)"
                )

        if self.pagination_buttons:
            btn_parts = []
            for btn in self.pagination_buttons:
                suffix = " (disabled)" if btn.disabled else ""
                btn_parts.append(f"[{btn.element_id}] {btn.direction}: {btn.text}{suffix}")
            lines.append(f"<pagination>{' | '.join(btn_parts)}</pagination>")

        return "\n".join(lines)


__all__ = [
    "AccessibilityNode",
    "Bounds",
    "DOMDiff",
    "DOMTreeNode",
    "IframeContentHint",
    "PaginationButton",
    "SemanticNode",
    "ElementState",
    "PageState",
    "ViewportState",
]
