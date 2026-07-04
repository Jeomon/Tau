"""Concrete widgets built on the Buffer/Rect/Widget render layer (see ``tau.tui.widget``).

Ratatui itself splits the ``Widget`` trait (its ``ratatui-core`` crate) from
its widget library (``ratatui-widgets``); this package is that split's Tau
counterpart. Everything here writes into a ``Buffer`` via ``Rect`` — none of
it touches ``Component``/``list[str]`` rendering in ``tau.tui.component``.

Exports are lazy (see ``tau.tui.__init__`` for why): nothing is imported
until a symbol is actually accessed via ``tau.tui.widgets``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.tui.widgets.barchart import Bar, BarChart, BarGroup
    from tau.tui.widgets.block import Block, Borders, Padding, Title, TitlePosition
    from tau.tui.widgets.calendar import CalendarEventStore, DateStyler, Monthly
    from tau.tui.widgets.canvas import Canvas, Map, MapResolution, Marker, Points, Rectangle
    from tau.tui.widgets.canvas import Line as CanvasLine
    from tau.tui.widgets.chart import Axis, Chart, Dataset, GraphType, LegendPosition
    from tau.tui.widgets.clear import Clear
    from tau.tui.widgets.gauge import Gauge, LineGauge
    from tau.tui.widgets.list import List, ListDirection, ListItem, ListState
    from tau.tui.widgets.paragraph import Paragraph, Wrap
    from tau.tui.widgets.scrollbar import Scrollbar, ScrollbarOrientation, ScrollbarState
    from tau.tui.widgets.sparkline import RenderDirection, Sparkline
    from tau.tui.widgets.table import Row, Table, TableState
    from tau.tui.widgets.tabs import Tabs

__all__ = [
    "Block",
    "Borders",
    "Padding",
    "Title",
    "TitlePosition",
    "Clear",
    "List",
    "ListDirection",
    "ListItem",
    "ListState",
    "Paragraph",
    "Wrap",
    "Scrollbar",
    "ScrollbarOrientation",
    "ScrollbarState",
    "Tabs",
    "Gauge",
    "LineGauge",
    "Table",
    "Row",
    "TableState",
    "Sparkline",
    "RenderDirection",
    "BarChart",
    "Bar",
    "BarGroup",
    "Canvas",
    "CanvasLine",
    "Points",
    "Rectangle",
    "Marker",
    "Map",
    "MapResolution",
    "Chart",
    "Dataset",
    "Axis",
    "LegendPosition",
    "GraphType",
    "Monthly",
    "DateStyler",
    "CalendarEventStore",
]

_SUBMODULE_OF = {
    "Block": "tau.tui.widgets.block",
    "Borders": "tau.tui.widgets.block",
    "Padding": "tau.tui.widgets.block",
    "Title": "tau.tui.widgets.block",
    "TitlePosition": "tau.tui.widgets.block",
    "Clear": "tau.tui.widgets.clear",
    "List": "tau.tui.widgets.list",
    "ListDirection": "tau.tui.widgets.list",
    "ListItem": "tau.tui.widgets.list",
    "ListState": "tau.tui.widgets.list",
    "Paragraph": "tau.tui.widgets.paragraph",
    "Wrap": "tau.tui.widgets.paragraph",
    "Scrollbar": "tau.tui.widgets.scrollbar",
    "ScrollbarOrientation": "tau.tui.widgets.scrollbar",
    "ScrollbarState": "tau.tui.widgets.scrollbar",
    "Tabs": "tau.tui.widgets.tabs",
    "Gauge": "tau.tui.widgets.gauge",
    "LineGauge": "tau.tui.widgets.gauge",
    "Table": "tau.tui.widgets.table",
    "Row": "tau.tui.widgets.table",
    "TableState": "tau.tui.widgets.table",
    "Sparkline": "tau.tui.widgets.sparkline",
    "RenderDirection": "tau.tui.widgets.sparkline",
    "BarChart": "tau.tui.widgets.barchart",
    "Bar": "tau.tui.widgets.barchart",
    "BarGroup": "tau.tui.widgets.barchart",
    "Canvas": "tau.tui.widgets.canvas",
    "Points": "tau.tui.widgets.canvas",
    "Rectangle": "tau.tui.widgets.canvas",
    "Marker": "tau.tui.widgets.canvas",
    "Map": "tau.tui.widgets.canvas",
    "MapResolution": "tau.tui.widgets.canvas",
    "Chart": "tau.tui.widgets.chart",
    "Dataset": "tau.tui.widgets.chart",
    "Axis": "tau.tui.widgets.chart",
    "LegendPosition": "tau.tui.widgets.chart",
    "GraphType": "tau.tui.widgets.chart",
    "Monthly": "tau.tui.widgets.calendar",
    "DateStyler": "tau.tui.widgets.calendar",
    "CalendarEventStore": "tau.tui.widgets.calendar",
}


def __getattr__(name: str) -> object:
    if name == "CanvasLine":
        module = importlib.import_module("tau.tui.widgets.canvas")
        value = module.Line
        globals()[name] = value
        return value
    module_path = _SUBMODULE_OF.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
