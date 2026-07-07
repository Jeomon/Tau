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

Because ``__getattr__``/``__all__`` (PEP 562) resolve lazily, ``tau.tui``
already doubles as its own prelude: ``from tau.tui import *`` or
``from tau.tui import Rect, Buffer, Widget, Layout, ...`` pulls in the
render-layer types on demand without a separate ``prelude`` submodule.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.backend import AnsiBackend, Backend, TestBackend
    from tau.tui.buffer import Buffer, Cell
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
    from tau.tui.frame import BufferedTerminal, Fixed, Frame, Fullscreen, Inline
    from tau.tui.geometry import Position, Rect
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
    from tau.tui.layout import Alignment, Constraint, Direction, Flex, Layout
    from tau.tui.markdown import render_markdown
    from tau.tui.palette import material, tailwind
    from tau.tui.style import RESET_COLOR, Color, Modifier, Style, Stylize, parse_color
    from tau.tui.terminal import (
        CellDimensions,
        Terminal,
        TerminalCapabilities,
        detect_capabilities,
        get_capabilities,
        get_cell_dimensions,
    )
    from tau.tui.testing import assert_buffer_eq
    from tau.tui.text import Line as TextLine
    from tau.tui.text import Masked, Span
    from tau.tui.text import Text as StyledText
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
    from tau.tui.service import TUI, OverlayHandle, OverlayOptions, Renderer
    from tau.tui.widget import StatefulWidget, Widget, render_widget

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
    # Buffer/Cell/Rect/Style/Widget (grid render layer)
    "Rect",
    "Position",
    "Buffer",
    "Cell",
    "Style",
    "Stylize",
    "Color",
    "RESET_COLOR",
    "parse_color",
    "Modifier",
    "Span",
    "Masked",
    "TextLine",
    "StyledText",
    "Widget",
    "StatefulWidget",
    "render_widget",
    "Backend",
    "TestBackend",
    "AnsiBackend",
    "assert_buffer_eq",
    "Frame",
    "BufferedTerminal",
    "Fullscreen",
    "Fixed",
    "Inline",
    # Layout constraint solver
    "Layout",
    "Constraint",
    "Direction",
    "Flex",
    "Alignment",
    # Palettes
    "tailwind",
    "material",
    # Layout (legacy Component-based)
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
    "Backend": "tau.tui.backend",
    "TestBackend": "tau.tui.backend",
    "AnsiBackend": "tau.tui.backend",
    "Frame": "tau.tui.frame",
    "BufferedTerminal": "tau.tui.frame",
    "Fullscreen": "tau.tui.frame",
    "Fixed": "tau.tui.frame",
    "Inline": "tau.tui.frame",
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
    "Rect": "tau.tui.geometry",
    "Position": "tau.tui.geometry",
    "Buffer": "tau.tui.buffer",
    "Cell": "tau.tui.buffer",
    "Style": "tau.tui.style",
    "RESET_COLOR": "tau.tui.style",
    "parse_color": "tau.tui.style",
    "Stylize": "tau.tui.style",
    "assert_buffer_eq": "tau.tui.testing",
    "Color": "tau.tui.style",
    "Modifier": "tau.tui.style",
    "Span": "tau.tui.text",
    "Masked": "tau.tui.text",
    "TextLine": "tau.tui.text",
    "StyledText": "tau.tui.text",
    "Widget": "tau.tui.widget",
    "StatefulWidget": "tau.tui.widget",
    "render_widget": "tau.tui.widget",
    "Layout": "tau.tui.layout",
    "Constraint": "tau.tui.layout",
    "Direction": "tau.tui.layout",
    "Flex": "tau.tui.layout",
    "Alignment": "tau.tui.layout",
    "tailwind": "tau.tui.palette",
    "material": "tau.tui.palette",
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
    "TUI": "tau.tui.service",
    "OverlayHandle": "tau.tui.service",
    "OverlayOptions": "tau.tui.service",
    "Renderer": "tau.tui.service",
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
