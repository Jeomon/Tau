"""Builds the compact desktop-state summary injected ephemerally into LLM
context each turn while the computer tool's desktop session is open."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tau.message.types import UserMessage

if TYPE_CHECKING:
    from .types import Desktop

ObservationMode = Literal["screenshot", "accessibility_tree", "both"]


def build_state_message(
    desktop: Desktop, mode: ObservationMode = "accessibility_tree", supports_image: bool = True
) -> UserMessage | None:
    """Live desktop summary: focused/open windows, plus a screenshot and/or
    the accessible interactive elements currently on screen, per `mode`.
    None while the desktop is closed, so nothing gets injected before
    action='open' is called.

    `supports_image` gates the screenshot on the active model's actual
    modality support — set from `Modality.Image in ctx.llm.model.input`,
    checked fresh every call since the model can change mid-session via
    /model. When a screenshot was requested but the model can't take images,
    the accessibility tree is included instead so the turn isn't left with
    no usable observation at all.
    """
    if not desktop.is_open:
        return None

    wants_screenshot = mode in ("screenshot", "both")
    wants_accessibility = mode in ("accessibility_tree", "both")
    use_screenshot = wants_screenshot and supports_image
    use_accessibility = wants_accessibility or (wants_screenshot and not supports_image)
    state = desktop.get_state(
        as_bytes=True, use_screenshot=use_screenshot, use_accessibility=use_accessibility
    )
    parts = [state.to_string()]

    if use_accessibility:
        elements_to_string = getattr(state.tree_state, "interactive_elements_to_string", None)
        if callable(elements_to_string):
            elements = elements_to_string()
            if elements:
                parts.append(f"Interactive elements (click at the given coords):\n{elements}")

    text = "\n\n".join(parts)
    if use_screenshot and state.screenshot is not None:
        return UserMessage.with_images(text, [state.screenshot])
    return UserMessage.from_text(text)
