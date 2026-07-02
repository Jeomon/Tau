"""Standalone terminal UI framework.

Application-specific layouts and runtime integration live in
``tau.modes.interactive``. This package contains only reusable terminal,
rendering, input, component, and theme primitives.
"""

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
from tau.tui.tui import (
    TUI,
    OverlayHandle,
    OverlayOptions,
    Renderer,
)

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
