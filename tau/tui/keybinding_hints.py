"""Utilities for formatting keybinding hints in TUI output."""

from __future__ import annotations

import sys

from tau.tui.input import get_keybindings
from tau.tui.utils import DIM, RESET


def _format_key(key: str) -> str:
    """Normalize a key combo string for display, mapping 'alt' → 'option' on macOS."""
    parts = key.split("+")
    if sys.platform == "darwin":
        parts = ["option" if p.lower() == "alt" else p for p in parts]
    return "+".join(parts)


def key_text(action: str) -> str:
    """Return the display string for all key combos bound to *action*.

    e.g. ``"ctrl+s / ctrl+enter"``
    Returns an empty string if the action has no bindings.
    """
    keys = get_keybindings().keys_for(action)
    if not keys:
        return ""
    return " / ".join(_format_key(k) for k in keys)


def key_hint(action: str, description: str) -> str:
    """Return ``DIM <keys> RESET <description>`` for inline footer hints."""
    k = key_text(action)
    if not k:
        return description
    return f"{DIM}{k}{RESET} {description}"


def raw_key_hint(key: str, description: str) -> str:
    """Same as :func:`key_hint` but accepts a raw key string instead of an action name."""
    return f"{DIM}{_format_key(key)}{RESET} {description}"
