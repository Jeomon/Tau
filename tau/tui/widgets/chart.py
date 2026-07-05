"""Chart: axis-based line/scatter plotting, mirroring ratatui's ``widgets::Chart``.

Distinct from ``Canvas`` (arbitrary shapes at arbitrary coordinates): ``Chart``
is specifically "plot labeled ``Dataset``s against X/Y axes with a legend" —
the more common need (e.g. a latency-over-time graph). Reuses ``Canvas``'s
Braille-subpixel grid for the actual plotting so datasets get the same
resolution as ``Canvas`` shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.widgets.canvas import Line as _CanvasLine
from tau.tui.widgets.canvas import _Grid
from tau.tui.widgets.symbols import BRAILLE_BASE


@dataclass(slots=True)
class Axis:
    title: str | None = None
    bounds: tuple[float, float] = (0.0, 1.0)
    labels: list[str] = field(default_factory=list)
    style: Style = field(default_factory=Style)


class GraphType(Enum):
    SCATTER = auto()  # each point drawn independently (default)
    LINE = auto()  # consecutive points connected
    BAR = auto()  # a vertical bar per point, down to fill_to_y
    AREA = auto()  # like LINE, plus the region between the line and fill_to_y filled in


@dataclass(slots=True)
class Dataset:
    name: str
    data: list[tuple[float, float]]
    style: Style = field(default_factory=Style)
    graph_type: GraphType = GraphType.SCATTER
    fill_to_y: float | None = None  # baseline for BAR/AREA; defaults to the y-axis's lower bound


class LegendPosition(Enum):
    TOP_LEFT = auto()
    TOP_RIGHT = auto()
    BOTTOM_LEFT = auto()
    BOTTOM_RIGHT = auto()
    NONE = auto()


@dataclass(slots=True)
class Chart:
    datasets: list[Dataset]
    x_axis: Axis = field(default_factory=Axis)
    y_axis: Axis = field(default_factory=Axis)
    legend_position: LegendPosition = LegendPosition.TOP_RIGHT

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return

        left_gutter = max((len(label) for label in self.y_axis.labels), default=0)
        if left_gutter:
            left_gutter += 1
        bottom_gutter = 1 if self.x_axis.labels else 0

        plot = Rect(
            area.left + left_gutter,
            area.top,
            max(0, area.width - left_gutter),
            max(0, area.height - bottom_gutter),
        )
        if plot.is_empty():
            return

        self._plot_datasets(plot, buf)
        self._render_y_labels(area, plot, left_gutter, buf)
        self._render_x_labels(area, plot, buf)
        if self.legend_position is not LegendPosition.NONE:
            self._render_legend(plot, buf)

    def _plot_datasets(self, plot: Rect, buf: Buffer) -> None:
        px_w, px_h = plot.width * 2, plot.height * 4
        grid = _Grid(px_w, px_h)
        x0, x1 = self.x_axis.bounds
        y0, y1 = self.y_axis.bounds
        xr, yr = (x1 - x0) or 1, (y1 - y0) or 1

        def to_px(dx: float, dy: float) -> tuple[int, int]:
            px = round((dx - x0) / xr * (px_w - 1))
            py = round((1 - (dy - y0) / yr) * (px_h - 1))
            return px, py

        for ds in self.datasets:
            pixels = [to_px(dx, dy) for dx, dy in ds.data]
            baseline_y = to_px(0, ds.fill_to_y if ds.fill_to_y is not None else y0)[1]

            if ds.graph_type is GraphType.SCATTER:
                for px, py in pixels:
                    grid.set(px, py, ds.style)
            elif ds.graph_type is GraphType.LINE:
                for (x1p, y1p), (x2p, y2p) in zip(pixels, pixels[1:], strict=False):
                    for canvas_x, canvas_y in _CanvasLine(x1p, y1p, x2p, y2p).points():
                        grid.set(int(canvas_x), int(canvas_y), ds.style)
            elif ds.graph_type is GraphType.BAR:
                for px, py in pixels:
                    lo, hi = sorted((py, baseline_y))
                    for yy in range(lo, hi + 1):
                        grid.set(px, yy, ds.style)
            else:  # AREA
                for (x1p, y1p), (x2p, y2p) in zip(pixels, pixels[1:], strict=False):
                    for canvas_x, canvas_y in _CanvasLine(x1p, y1p, x2p, y2p).points():
                        pixel_x, pixel_y = int(canvas_x), int(canvas_y)
                        lo, hi = sorted((pixel_y, baseline_y))
                        for yy in range(lo, hi + 1):
                            grid.set(pixel_x, yy, ds.style)

        cell_w = px_w // 2
        for cy in range(plot.height):
            for cx in range(cell_w):
                idx = cy * cell_w + cx
                bits = grid.dots[idx]
                if bits == 0:
                    continue
                buf.set(plot.left + cx, plot.top + cy, chr(BRAILLE_BASE + bits), grid.styles[idx])

    def _render_y_labels(self, area: Rect, plot: Rect, left_gutter: int, buf: Buffer) -> None:
        labels = self.y_axis.labels
        if not labels or left_gutter <= 0:
            return
        n = len(labels)
        for i, label in enumerate(labels):
            y = plot.top if n == 1 else plot.top + round(i * (plot.height - 1) / (n - 1))
            text = label.rjust(left_gutter - 1)
            buf.set_string(area.left, y, text, self.y_axis.style, left_gutter - 1)

    def _render_x_labels(self, area: Rect, plot: Rect, buf: Buffer) -> None:
        labels = self.x_axis.labels
        if not labels or area.bottom <= plot.bottom:
            return
        n, y = len(labels), area.bottom - 1
        for i, label in enumerate(labels):
            span = plot.width - len(label)
            x = plot.left if n == 1 else plot.left + round(i * span / (n - 1))
            x = max(plot.left, x)
            buf.set_string(x, y, label, self.x_axis.style, max(0, plot.right - x))

    def _render_legend(self, plot: Rect, buf: Buffer) -> None:
        names = [ds for ds in self.datasets if ds.name]
        if not names:
            return
        width = max(len(ds.name) for ds in names) + 2
        top = self.legend_position in (LegendPosition.TOP_LEFT, LegendPosition.TOP_RIGHT)
        left = self.legend_position in (LegendPosition.TOP_LEFT, LegendPosition.BOTTOM_LEFT)
        y0 = plot.top if top else max(plot.top, plot.bottom - len(names))
        x0 = plot.left if left else max(plot.left, plot.right - width)

        for i, ds in enumerate(names):
            y = y0 + i
            if y >= plot.bottom:
                break
            buf.set_string(x0, y, ds.name, ds.style, width)
