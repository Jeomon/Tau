"""Standalone terminal UI framework.

Application-specific layouts and runtime integration live in
``tau.modes.interactive``. This package contains only reusable terminal,
rendering, input, component, and theme primitives.

Re-exports below are lazy (`PEP 562 <https://peps.python.org/pep-0562/>`_):
no submodule is imported until one of its symbols is actually accessed via
``tau.tui``. Nothing in this codebase imports from the package root today
(everything uses direct submodule imports, e.g. ``tau.tui.terminal``), but
Python still executes this file whenever *any* submodule is imported —
eagerly pulling in mistletoe, every component, and the terminal/theme
machinery cost real startup time for callers who only wanted one small
submodule (e.g. ``tau.tui.utils``).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.component import (
        Column,
        Columns,
        Component,
        Constrained,
        Container,
        Focusable,
        Row,
        Rows,
        StaticComponent,
        Text,
        VerticalStack,
    )
    from tau.tui.components.box import Box, DynamicBorder
    from tau.tui.components.editor import EditorComponent, EditorExtras
    from tau.tui.components.image import Image, ImageDimensions, ImageOptions
    from tau.tui.components.select_list import InlineSelector, SelectItem, SelectList
    from tau.tui.components.spinner import Spinner
    from tau.tui.components.text_input import TextInput
    from tau.tui.input import (
        BgColorEvent,
        FocusEvent,
        InputEvent,
        InputParser,
        Key,
        KeybindingsManager,
        KeyEvent,
        KeyMap,
        MouseEvent,
        PasteEvent,
        configure_keybindings,
        get_keybindings,
    )
    from tau.tui.markdown import render_markdown
    from tau.tui.terminal import (
        CellDimensions,
        Terminal,
        TerminalCapabilities,
        detect_capabilities,
        get_capabilities,
        get_cell_dimensions,
    )
    from tau.tui.theme import (
        ColorFn,
        InputTheme,
        LayoutTheme,
        MarkdownTheme,
        MessageTheme,
        SelectListTheme,
        SpinnerTheme,
        color,
        rgb,
        rgb_bold,
        rgb_italic,
    )
    from tau.tui.tui import TUI, OverlayHandle, OverlayOptions, Renderer

__all__ = [
    # Application and rendering
    "TUI",
    "Renderer",
    "Terminal",
    "TerminalCapabilities",
    "CellDimensions",
    "detect_capabilities",
    "get_capabilities",
    "get_cell_dimensions",
    # Components
    "Component",
    "Focusable",
    "Container",
    "StaticComponent",
    "Text",
    "TextInput",
    "EditorComponent",
    "EditorExtras",
    "Spinner",
    "Image",
    "ImageDimensions",
    "ImageOptions",
    "SelectList",
    "SelectItem",
    "InlineSelector",
    "Box",
    "DynamicBorder",
    # Layout
    "Column",
    "Row",
    "Rows",
    "Columns",
    "Constrained",
    "VerticalStack",
    # Overlays
    "OverlayOptions",
    "OverlayHandle",
    # Input
    "InputEvent",
    "InputParser",
    "Key",
    "KeyEvent",
    "PasteEvent",
    "MouseEvent",
    "BgColorEvent",
    "FocusEvent",
    "KeyMap",
    "KeybindingsManager",
    "get_keybindings",
    "configure_keybindings",
    # Themes and markdown
    "ColorFn",
    "LayoutTheme",
    "SpinnerTheme",
    "MarkdownTheme",
    "MessageTheme",
    "InputTheme",
    "SelectListTheme",
    "color",
    "rgb",
    "rgb_bold",
    "rgb_italic",
    "render_markdown",
]

_SUBMODULE_OF = {
    "Column": "tau.tui.component",
    "Columns": "tau.tui.component",
    "Component": "tau.tui.component",
    "Constrained": "tau.tui.component",
    "Container": "tau.tui.component",
    "Focusable": "tau.tui.component",
    "Row": "tau.tui.component",
    "Rows": "tau.tui.component",
    "StaticComponent": "tau.tui.component",
    "Text": "tau.tui.component",
    "VerticalStack": "tau.tui.component",
    "Box": "tau.tui.components.box",
    "DynamicBorder": "tau.tui.components.box",
    "EditorComponent": "tau.tui.components.editor",
    "EditorExtras": "tau.tui.components.editor",
    "Image": "tau.tui.components.image",
    "ImageDimensions": "tau.tui.components.image",
    "ImageOptions": "tau.tui.components.image",
    "InlineSelector": "tau.tui.components.select_list",
    "SelectItem": "tau.tui.components.select_list",
    "SelectList": "tau.tui.components.select_list",
    "Spinner": "tau.tui.components.spinner",
    "TextInput": "tau.tui.components.text_input",
    "BgColorEvent": "tau.tui.input",
    "FocusEvent": "tau.tui.input",
    "InputEvent": "tau.tui.input",
    "InputParser": "tau.tui.input",
    "Key": "tau.tui.input",
    "KeybindingsManager": "tau.tui.input",
    "KeyEvent": "tau.tui.input",
    "KeyMap": "tau.tui.input",
    "MouseEvent": "tau.tui.input",
    "PasteEvent": "tau.tui.input",
    "configure_keybindings": "tau.tui.input",
    "get_keybindings": "tau.tui.input",
    "render_markdown": "tau.tui.markdown",
    "CellDimensions": "tau.tui.terminal",
    "Terminal": "tau.tui.terminal",
    "TerminalCapabilities": "tau.tui.terminal",
    "detect_capabilities": "tau.tui.terminal",
    "get_capabilities": "tau.tui.terminal",
    "get_cell_dimensions": "tau.tui.terminal",
    "ColorFn": "tau.tui.theme",
    "InputTheme": "tau.tui.theme",
    "LayoutTheme": "tau.tui.theme",
    "MarkdownTheme": "tau.tui.theme",
    "MessageTheme": "tau.tui.theme",
    "SelectListTheme": "tau.tui.theme",
    "SpinnerTheme": "tau.tui.theme",
    "color": "tau.tui.theme",
    "rgb": "tau.tui.theme",
    "rgb_bold": "tau.tui.theme",
    "rgb_italic": "tau.tui.theme",
    "TUI": "tau.tui.tui",
    "OverlayHandle": "tau.tui.tui",
    "OverlayOptions": "tau.tui.tui",
    "Renderer": "tau.tui.tui",
}


def __getattr__(name: str) -> object:
    module_path = _SUBMODULE_OF.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value  # cache on the module so repeat access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
