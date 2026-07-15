"""Selects the Desktop backend for the current operating system."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any, Literal

PlatformName = Literal["macos", "windows"]

_PLATFORM_PACKAGES: dict[PlatformName, str] = {
    "macos": "tau.builtins.extensions.computer_use.macos",
    "windows": "tau.builtins.extensions.computer_use.windows",
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
    """Import and return the backend package for the current operating system."""
    name = get_platform_name(platform)
    return import_module(_PLATFORM_PACKAGES[name])


def get_desktop_class(platform: str | None = None) -> type[Any]:
    """Return the current platform's Desktop implementation class."""
    name = get_platform_name(platform)
    module = import_module(f"{_PLATFORM_PACKAGES[name]}.desktop")
    return module.Desktop


__all__ = ["get_desktop_class", "get_platform_name", "get_platform_package"]
