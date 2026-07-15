"""Selects the Desktop backend for the current operating system."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any, Literal

PlatformName = Literal["macos", "windows"]

_PLATFORM_PACKAGES: dict[PlatformName, str] = {
    "macos": "macos",
    "windows": "windows",
}


def get_platform_name(platform: str | None = None) -> PlatformName:
    """Return the computer_use backend name for a Python sys.platform string."""
    platform = platform or sys.platform
    if platform == "darwin":
        return "macos"
    if platform in ("win32", "cygwin"):
        return "windows"
    raise RuntimeError(f"Unsupported computer_use platform: {platform}")


def get_platform_package(platform: str | None = None) -> ModuleType:
    """Import and return the backend package for the current operating system.

    Imported relative to this module's own package rather than by a fixed
    absolute dotted path: the extension loader gives this package a synthetic
    module name that changes depending on where it's discovered from (builtin,
    project, or global), so an absolute ``tau.builtins...`` path only works
    when the extension happens to live inside the installed tau package.
    """
    name = get_platform_name(platform)
    return import_module(f".{_PLATFORM_PACKAGES[name]}", package=__package__)


def get_desktop_class(platform: str | None = None) -> type[Any]:
    """Return the current platform's Desktop implementation class."""
    name = get_platform_name(platform)
    module = import_module(f".{_PLATFORM_PACKAGES[name]}.desktop", package=__package__)
    return module.Desktop


__all__ = ["get_desktop_class", "get_platform_name", "get_platform_package"]
