"""Concrete widgets built on the string render contract.

The core rendering contract is separate from this widget library. Everything
here returns styled ANSI lines, computing column positions itself (``List``
highlights a row's span, ``Block`` draws a border, ``Tabs`` positions
dividers) using the width-aware helpers in ``tau.tui.ansi_text``.

``Rect`` is still used for geometry — ``Block.inner(area)`` shrinks a
rectangle past its borders so callers can lay out inside it — but nothing
here writes into a cell grid. Frame rendering goes through lines end to end;
see ``tau.tui.scrollback``.

Exports are lazy (see ``tau.tui.__init__`` for why): nothing is imported
until a symbol is actually accessed via ``tau.tui.widgets``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.widgets.block import Block, Borders, Padding, Title, TitlePosition
    from tau.tui.widgets.list import List, ListDirection, ListItem, ListState
    from tau.tui.widgets.tabs import Tabs

__all__ = [
    "Block",
    "Borders",
    "List",
    "ListDirection",
    "ListItem",
    "ListState",
    "Padding",
    "Tabs",
    "Title",
    "TitlePosition",
]

_SUBMODULE_OF = {
    "Block": "tau.tui.widgets.block",
    "Borders": "tau.tui.widgets.block",
    "Padding": "tau.tui.widgets.block",
    "Title": "tau.tui.widgets.block",
    "TitlePosition": "tau.tui.widgets.block",
    "List": "tau.tui.widgets.list",
    "ListDirection": "tau.tui.widgets.list",
    "ListItem": "tau.tui.widgets.list",
    "ListState": "tau.tui.widgets.list",
    "Tabs": "tau.tui.widgets.tabs",
}


def __getattr__(name: str) -> object:
    module_path = _SUBMODULE_OF.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
