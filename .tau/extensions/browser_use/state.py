"""Builds the live browser-state message injected ephemerally into LLM
context each turn while the browser session is open."""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING, Any, Literal

from tau.message.types import UserMessage

if TYPE_CHECKING:
    from .session import BrowserSession

ObservationMode = Literal["screenshot", "accessibility_tree", "both"]

_MAX_ELEMENTS = 150
_TEXT_LIMIT = 80
_VALUE_LIMIT = 40


async def build_state_message(
    session: BrowserSession,
    mode: ObservationMode = "both",
    supports_image: bool = True,
) -> UserMessage | None:
    """Live page summary: tabs, URL, viewport, the interactive accessibility
    tree, and/or an annotated screenshot, per `mode`. None while the browser is
    closed, so nothing gets injected before action='open' is called.

    `supports_image` gates the screenshot on the active model's modality
    support, checked fresh every call since /model can switch models
    mid-session. When a screenshot was requested but the model can't take
    images, the accessibility tree is included instead so the turn still gets a
    usable observation.
    """
    if not session.is_open:
        return None

    wants_screenshot = mode in ("screenshot", "both")
    wants_tree = mode in ("accessibility_tree", "both")
    use_screenshot = wants_screenshot and supports_image
    use_tree = wants_tree or (wants_screenshot and not supports_image)

    try:
        state = await session.capture(include_screenshot=use_screenshot)
        tabs = await session.tabs()
    except Exception as exc:
        return UserMessage.from_text(
            f"Browser state (session open, but capture failed: {exc})"
        )

    parts = [_summary(state, tabs)]
    if use_tree:
        roots = getattr(state, "dom_roots", ())
        if roots:
            # Indented tree: nesting conveys page structure to the model.
            rendered = _format_ax_tree(roots)
        else:
            rendered = _format_ax_flat(state.elements)
        heading = (
            "Interactive accessibility tree "
            "(role \"name\" [states]; indented by nesting; click by element_id):"
        )
        if rendered:
            parts.append(heading + "\n" + rendered)

    text = "\n\n".join(parts)
    if use_screenshot and state.screenshot:
        image = _annotated_screenshot(state)
        if image is not None:
            return UserMessage.with_images(text, [image])
    return UserMessage.from_text(text)


def _summary(state: Any, tabs: list[tuple[str, str, str, bool]]) -> str:
    lines = ["Browser state:"]
    if len(tabs) > 1:
        lines.append("Tabs:")
        for target_id, url, title, current in tabs:
            marker = "* " if current else "  "
            lines.append(f"{marker}[{target_id}] {title!r} {url}")
    viewport = state.viewport
    lines.append(f"Page: {state.title!r} — {state.url}")
    lines.append(
        f"Viewport: {viewport.width:g}x{viewport.height:g}, "
        f"scrolled to ({viewport.page_x:g}, {viewport.page_y:g})"
    )
    hidden = sum(hint.hidden_interactive_count for hint in state.iframe_hints)
    if hidden:
        lines.append(
            f"Note: {hidden} interactive element(s) inside iframes are not listed; "
            "scroll or interact to reveal them."
        )
    return "\n".join(lines)


def _ax_states(element: Any) -> str:
    """The interactive AX states worth surfacing — checked/expanded/pressed/
    selected/disabled/required, a haspopup hint, and the current value of an
    editable control — as a ` [a, b]` suffix (empty when there are none)."""
    ax = getattr(element, "accessibility", None)
    states: list[str] = []
    if ax is not None:
        if ax.checked in (True, "true"):
            states.append("checked")
        elif ax.checked == "mixed":
            states.append("mixed")
        if ax.expanded is True:
            states.append("expanded")
        elif ax.expanded is False:
            states.append("collapsed")
        if ax.pressed in (True, "true"):
            states.append("pressed")
        elif ax.pressed == "mixed":
            states.append("pressed:mixed")
        if ax.selected is True:
            states.append("selected")
        if ax.disabled:
            states.append("disabled")
        if ax.required:
            states.append("required")
        if ax.haspopup:
            states.append(f"haspopup={ax.haspopup}")
        if ax.value not in (None, "", False):
            value = " ".join(str(ax.value).split())
            if len(value) > _VALUE_LIMIT:
                value = value[: _VALUE_LIMIT - 1] + "…"
            states.append(f'value="{value}"')
    return f" [{', '.join(states)}]" if states else ""


def _ax_line(element: Any) -> str:
    """One `[id] role "name" [states] (flags)` accessibility-node line. Role and
    name fall back to the tag name / visible text when the element has no AX
    entry (e.g. a cursor:pointer <div> with no ARIA role)."""
    ax = getattr(element, "accessibility", None)
    role = (ax.role if ax and ax.role else "") or element.tag_name
    name = " ".join(((ax.name if ax and ax.name else "") or element.text or "").split())
    if len(name) > _TEXT_LIMIT:
        name = name[: _TEXT_LIMIT - 1] + "…"
    flags = []
    if element.scrollable:
        flags.append("scrollable")
    if getattr(element, "shadow_root_type", None):
        flags.append("shadow")
    flag_text = f" ({', '.join(flags)})" if flags else ""
    return f"[{element.backend_node_id}] {role} {name!r}{_ax_states(element)}{flag_text}"


def _format_ax_flat(elements: tuple[Any, ...]) -> str:
    lines = [_ax_line(element) for element in elements[:_MAX_ELEMENTS]]
    if len(elements) > _MAX_ELEMENTS:
        lines.append(f"… and {len(elements) - _MAX_ELEMENTS} more elements (scroll to see them)")
    return "\n".join(lines)


def _format_ax_tree(roots: tuple[Any, ...]) -> str:
    """Render the interactive-element tree (state.dom_roots) as an indented
    accessibility outline — nesting shows the model page structure (a search
    landmark over its button, a card and the controls inside it, iframe content
    under its host) while each line is `[id] role "name" [states]` the agent
    clicks by. Capped at _MAX_ELEMENTS lines; deeper/overflow nodes summarised."""
    lines: list[str] = []
    dropped = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal dropped
        if len(lines) >= _MAX_ELEMENTS:
            dropped += 1 + _count(node) - 1  # this node + its subtree not shown
            return
        lines.append("  " * depth + _ax_line(node.element))
        for child in node.children:
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    if dropped > 0:
        lines.append(f"… and {dropped} more elements (scroll to see them)")
    return "\n".join(lines)


def _count(node: Any) -> int:
    return 1 + sum(_count(child) for child in node.children)


def _annotated_screenshot(state: Any) -> bytes | None:
    """Screenshot resized to CSS-viewport resolution (halves HiDPI token
    cost) with a labeled bounding box on every clickable element, so the
    [id] labels in the element list are visually groundable. Falls back to
    the raw screenshot when Pillow is unavailable."""
    raw = base64.b64decode(state.screenshot)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return raw

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    viewport = state.viewport
    if viewport.width and image.width > viewport.width:
        image = image.resize(
            (round(viewport.width), round(image.height * viewport.width / image.width))
        )
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=11)
    except TypeError:
        font = ImageFont.load_default()

    palette = (
        (229, 57, 53),
        (30, 136, 229),
        (67, 160, 71),
        (251, 140, 0),
        (142, 36, 170),
        (0, 137, 123),
    )
    for index, element in enumerate(
        element for element in state.elements if element.clickable
    ):
        color = palette[index % len(palette)]
        bounds = element.bounds
        x0, y0 = bounds.x, bounds.y
        x1, y1 = x0 + bounds.width, y0 + bounds.height
        if x1 <= 0 or y1 <= 0 or x0 >= image.width or y0 >= image.height:
            continue
        draw.rectangle((x0, y0, x1, y1), outline=color, width=1)

        label = str(element.backend_node_id)
        text_bbox = draw.textbbox((0, 0), label, font=font)
        label_width = text_bbox[2] - text_bbox[0] + 4
        label_height = text_bbox[3] - text_bbox[1] + 4
        label_y = y0 - label_height if y0 - label_height >= 0 else y0
        draw.rectangle((x0, label_y, x0 + label_width, label_y + label_height), fill=color)
        draw.text((x0 + 2, label_y + 1), label, fill=(255, 255, 255), font=font)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
