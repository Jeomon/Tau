"""Canvas: arbitrary point/line plotting.

Six ``Marker`` styles: ``DOT``/``BLOCK``/``BAR`` place one
glyph per terminal cell; ``HALF_BLOCK`` (1x2), ``QUADRANT`` (2x2), and
``BRAILLE`` (2x4, default, highest resolution) pack a subpixel grid into
each cell. Not built: ``Sextant``/``Octant`` markers — verified
there's no reliable source for their exact Unicode codepoint layout (unlike
Braille/Quadrant, which follow documented, checkable tables).

``Map``/``MapResolution`` implement the shape's API and plug into ``Canvas``
like any other shape, but ship with a tiny hand-drawn placeholder outline,
not surveyed coastline data (no reliable source for that either) — pass
``Map(outlines=...)`` with real coordinates for anything that needs actual
geography.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style
from tau.tui.widgets.symbols import (
    BRAILLE_BASE,
    BRAILLE_BITS,
    HALF_BLOCK_BITS,
    HALF_BLOCK_GLYPHS,
    QUADRANT_BITS,
    QUADRANT_GLYPHS,
)


class Marker(Enum):
    DOT = auto()
    BLOCK = auto()
    BAR = auto()
    HALF_BLOCK = auto()
    QUADRANT = auto()
    BRAILLE = auto()


_MARKER_GLYPH = {Marker.DOT: "•", Marker.BLOCK: "█", Marker.BAR: "▄"}

# (cell width in subpixels, cell height in subpixels, bit-weight table, glyph lookup)
_SUBPIXEL_MARKERS = {
    Marker.HALF_BLOCK: (1, 2, HALF_BLOCK_BITS, HALF_BLOCK_GLYPHS),
    Marker.QUADRANT: (2, 2, QUADRANT_BITS, QUADRANT_GLYPHS),
}


@dataclass(slots=True)
class Points:
    """A shape: a list of ``(x, y)`` points in the canvas's own data coordinate space."""

    coords: list[tuple[float, float]]
    style: Style = field(default_factory=Style)


@dataclass(slots=True)
class Line:
    """A shape: a straight segment from ``(x1, y1)`` to ``(x2, y2)`` (Bresenham-rasterized)."""

    x1: float
    y1: float
    x2: float
    y2: float
    style: Style = field(default_factory=Style)

    def points(self) -> list[tuple[float, float]]:
        x1, y1, x2, y2 = round(self.x1), round(self.y1), round(self.x2), round(self.y2)
        dx, dy = abs(x2 - x1), -abs(y2 - y1)
        sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
        err = dx + dy
        out: list[tuple[float, float]] = []
        x, y = x1, y1
        while True:
            out.append((x, y))
            if x == x2 and y == y2:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
        return out


@dataclass(slots=True)
class Rectangle:
    """A shape: the outline of a rectangle in data-space (four Bresenham-rasterized edges)."""

    x: float
    y: float
    width: float
    height: float
    style: Style = field(default_factory=Style)

    def points(self) -> list[tuple[float, float]]:
        x1, y1, x2, y2 = self.x, self.y, self.x + self.width, self.y + self.height
        edges = [
            Line(x1, y1, x2, y1, self.style),
            Line(x2, y1, x2, y2, self.style),
            Line(x2, y2, x1, y2, self.style),
            Line(x1, y2, x1, y1, self.style),
        ]
        pts: list[tuple[float, float]] = []
        for edge in edges:
            pts.extend(edge.points())
        return pts


class MapResolution(Enum):
    LOW = auto()
    HIGH = auto()  # accepted for API parity; Tau has no high-res dataset, behaves same as LOW


# Rough, hand-drawn continent silhouettes in (longitude, latitude) degrees —
# NOT surveyed coastline data. Good enough to sanity-check that a Map shape
# plots roughly where landmasses are; wrong for anything that needs actual
# geography. Pass `Map(outlines=...)` with a real dataset (e.g. Natural
# Earth's 110m coastline export reduced to (lon, lat) point lists) instead.
_PLACEHOLDER_OUTLINES: list[list[tuple[float, float]]] = [
    [(-160, 70), (-100, 72), (-70, 50), (-80, 25), (-100, 15), (-120, 30), (-140, 55), (-160, 70)],
    [(-80, 10), (-35, -5), (-55, -55), (-75, -45), (-80, 10)],
    [(-15, 35), (35, 32), (50, 10), (40, -35), (15, -35), (-10, 5), (-15, 35)],
    [(-10, 40), (40, 45), (100, 70), (140, 60), (130, 35), (70, 10), (30, 35), (-10, 40)],
    [(115, -15), (150, -12), (153, -28), (140, -38), (115, -32), (115, -15)],
]


@dataclass(slots=True)
class Map:
    """A shape: continent outlines for a Canvas in (longitude, latitude) data-space.

    Defaults to a tiny built-in placeholder silhouette (see the module-level
    comment on ``_PLACEHOLDER_OUTLINES`` — not surveyed data). Pass
    ``outlines=`` with real coordinates for anything that needs actual
    geography; typical bounds are ``x_bounds=(-180, 180)``,
    ``y_bounds=(-90, 90)`` on the owning ``Canvas``.
    """

    resolution: MapResolution = MapResolution.LOW
    outlines: list[list[tuple[float, float]]] | None = None
    style: Style = field(default_factory=Style)

    def points(self) -> list[tuple[float, float]]:
        source = self.outlines if self.outlines is not None else _PLACEHOLDER_OUTLINES
        pts: list[tuple[float, float]] = []
        for polyline in source:
            for (x1, y1), (x2, y2) in zip(polyline, polyline[1:], strict=False):
                pts.extend(Line(x1, y1, x2, y2, self.style).points())
        return pts


Shape = Points | Line | Rectangle | Map


def _points_of(shape: Shape) -> list[tuple[float, float]]:
    return shape.coords if isinstance(shape, Points) else shape.points()


@dataclass(slots=True)
class _Grid:
    width: int
    height: int
    dots: list[int] = field(default_factory=list)
    styles: dict[int, Style] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dots:
            self.dots = [0] * (self.width * self.height)

    def set(self, px: int, py: int, style: Style) -> None:
        if not (0 <= px < self.width and 0 <= py < self.height):
            return
        cell_x, cell_y = px // 2, py // 4
        sub_x, sub_y = px % 2, py % 4
        idx = cell_y * (self.width // 2) + cell_x
        self.dots[idx] |= BRAILLE_BITS[sub_y][sub_x]
        self.styles[idx] = style


@dataclass(slots=True)
class _SubpixelGrid:
    """A cell grid packing ``cell_w`` x ``cell_h`` subpixels per cell, for HalfBlock/Quadrant."""

    px_w: int
    px_h: int
    cell_w: int
    cell_h: int
    weights: tuple[tuple[int, ...], ...]
    dots: list[int] = field(default_factory=list)
    styles: dict[int, Style] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dots:
            cols, rows = self.px_w // self.cell_w, self.px_h // self.cell_h
            self.dots = [0] * (cols * rows)

    def set(self, px: int, py: int, style: Style) -> None:
        if not (0 <= px < self.px_w and 0 <= py < self.px_h):
            return
        cols = self.px_w // self.cell_w
        cell_x, cell_y = px // self.cell_w, py // self.cell_h
        sub_x, sub_y = px % self.cell_w, py % self.cell_h
        idx = cell_y * cols + cell_x
        self.dots[idx] |= self.weights[sub_y][sub_x]
        self.styles[idx] = style


@dataclass(slots=True)
class Canvas:
    """A drawing surface: ``x_bounds``/``y_bounds`` map data space onto the widget's ``Rect``."""

    shapes: list[Shape]
    x_bounds: tuple[float, float] = (0.0, 1.0)
    y_bounds: tuple[float, float] = (0.0, 1.0)
    marker: Marker = Marker.BRAILLE
    background_style: Style = field(default_factory=Style)

    def render(self, area: Rect, buf: Buffer) -> None:
        if area.is_empty():
            return
        if self.marker is Marker.BRAILLE:
            self._render_braille(area, buf)
        elif self.marker in _SUBPIXEL_MARKERS:
            self._render_subpixel(area, buf)
        else:
            self._render_simple(area, buf)

    def _render_subpixel(self, area: Rect, buf: Buffer) -> None:
        cell_w, cell_h, weights, glyphs = _SUBPIXEL_MARKERS[self.marker]
        px_w, px_h = area.width * cell_w, area.height * cell_h
        grid = _SubpixelGrid(px_w, px_h, cell_w, cell_h, weights)
        x0, x1 = self.x_bounds
        y0, y1 = self.y_bounds
        xr, yr = (x1 - x0) or 1, (y1 - y0) or 1

        for shape in self.shapes:
            for dx, dy in _points_of(shape):
                px = round((dx - x0) / xr * (px_w - 1))
                py = round((1 - (dy - y0) / yr) * (px_h - 1))
                grid.set(px, py, shape.style)

        cols = px_w // cell_w
        for cy in range(area.height):
            for cx in range(cols):
                idx = cy * cols + cx
                bits = grid.dots[idx]
                if bits == 0:
                    continue
                buf.set(area.left + cx, area.top + cy, glyphs[bits], grid.styles[idx])

    def _render_braille(self, area: Rect, buf: Buffer) -> None:
        px_w, px_h = area.width * 2, area.height * 4
        grid = _Grid(px_w, px_h)
        x0, x1 = self.x_bounds
        y0, y1 = self.y_bounds
        xr, yr = (x1 - x0) or 1, (y1 - y0) or 1

        for shape in self.shapes:
            for dx, dy in _points_of(shape):
                px = round((dx - x0) / xr * (px_w - 1))
                py = round((1 - (dy - y0) / yr) * (px_h - 1))
                grid.set(px, py, shape.style)

        cell_w = px_w // 2
        for cy in range(area.height):
            for cx in range(cell_w):
                idx = cy * cell_w + cx
                bits = grid.dots[idx]
                if bits == 0:
                    continue
                buf.set(area.left + cx, area.top + cy, chr(BRAILLE_BASE + bits), grid.styles[idx])

    def _render_simple(self, area: Rect, buf: Buffer) -> None:
        glyph = _MARKER_GLYPH[self.marker]
        x0, x1 = self.x_bounds
        y0, y1 = self.y_bounds
        xr, yr = (x1 - x0) or 1, (y1 - y0) or 1

        for shape in self.shapes:
            for dx, dy in _points_of(shape):
                px = round((dx - x0) / xr * (area.width - 1))
                py = round((1 - (dy - y0) / yr) * (area.height - 1))
                if 0 <= px < area.width and 0 <= py < area.height:
                    buf.set(area.left + px, area.top + py, glyph, shape.style)
