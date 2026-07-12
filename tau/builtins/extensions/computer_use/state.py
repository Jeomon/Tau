"""Builds the compact desktop-state summary injected ephemerally into LLM
context each turn while the computer tool's desktop session is open."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Desktop


def build_state_message(desktop: Desktop) -> str | None:
    """Live desktop summary: focused/open windows plus the accessible
    interactive elements currently on screen. None while the desktop is
    closed, so nothing gets injected before action='open' is called."""
    if not desktop.is_open:
        return None

    state = desktop.get_state()
    parts = [state.to_string()]

    elements_to_string = getattr(state.tree_state, "interactive_elements_to_string", None)
    if callable(elements_to_string):
        elements = elements_to_string()
        if elements:
            parts.append(f"Interactive elements (click at the given coords):\n{elements}")

    return "\n\n".join(parts)
