# Public API — import from here instead of individual submodules.
#
#   Theme types:   from tau.tui import LayoutTheme, SpinnerTheme, MessageTheme
#   Keybindings:   from tau.tui import configure_keybindings, get_keybindings
#   Components:    from tau.tui import SelectList, SelectItem, Box
#
#   Themes:        from tau.themes.registry import theme_registry

from tau.tui.component import (
    Column,
    Columns,
    Constrained,
    Container,
    Row,
    Rows,
)
from tau.tui.components.box import Box
from tau.tui.components.select_list import SelectItem, SelectList
from tau.tui.keybindings import (
    KeybindingsManager,
    KeyMap,
    configure_keybindings,
    get_keybindings,
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

__all__ = [
    # Theme types
    "ColorFn",
    "LayoutTheme",
    "SpinnerTheme",
    "MarkdownTheme",
    "MessageTheme",
    "InputTheme",
    "SelectListTheme",
    # Keybindings
    "KeyMap",
    "KeybindingsManager",
    "get_keybindings",
    "configure_keybindings",
    # Color-function builders
    "color",
    "rgb",
    "rgb_bold",
    "rgb_italic",
    # Components
    "SelectList",
    "SelectItem",
    "Box",
    # Layout primitives
    "Container",
    "Column",
    "Row",
    "Rows",
    "Columns",
    "Constrained",
]
