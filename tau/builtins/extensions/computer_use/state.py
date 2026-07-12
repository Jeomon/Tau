"""Builds the compact desktop-state summary injected ephemerally into LLM
context each turn while the computer tool's desktop session is open."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tau.message.types import UserMessage

if TYPE_CHECKING:
    from .types import Desktop

ObservationMode = Literal["screenshot", "accessibility_tree", "both"]


def build_state_message(desktop: Desktop, mode: ObservationMode = "accessibility_tree") -> UserMessage | None:
    """Live desktop summary: focused/open windows, plus a screenshot and/or
    the accessible interactive elements currently on screen, per `mode`.
    None while the desktop is closed, so nothing gets injected before
    action='open' is called."""
    if not desktop.is_open:
        return None

    use_screenshot = mode in ("screenshot", "both")
    use_accessibility = mode in ("accessibility_tree", "both")
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
