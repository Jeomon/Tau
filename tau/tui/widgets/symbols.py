"""Character sets shared by widgets.

Keeping these as named sets (rather than hardcoding a glyph per widget, as
``components/box.py``'s single ``─`` divider does today) is what lets
``Block``/``Scrollbar`` offer a `border_type=` choice instead of a fork.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BorderSet:
    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    vertical: str
    horizontal: str


PLAIN = BorderSet("┌", "┐", "└", "┘", "│", "─")
ROUNDED = BorderSet("╭", "╮", "╰", "╯", "│", "─")
DOUBLE = BorderSet("╔", "╗", "╚", "╝", "║", "═")
THICK = BorderSet("┏", "┓", "┗", "┛", "┃", "━")

# One-eighth-resolution horizontal fill, empty -> full (Gauge/LineGauge).
FILL_HORIZONTAL = " ▏▎▍▌▋▊▉█"

# One-eighth-resolution vertical levels, empty -> full (Sparkline/BarChart).
FILL_VERTICAL = " ▁▂▃▄▅▆▇█"


@dataclass(frozen=True, slots=True)
class ScrollbarSet:
    track: str
    thumb: str
    begin: str
    end: str


SCROLLBAR_VERTICAL = ScrollbarSet(track="│", thumb="█", begin="▲", end="▼")
SCROLLBAR_HORIZONTAL = ScrollbarSet(track="─", thumb="█", begin="◄", end="►")

# Braille dot bit layout (2 cols x 4 rows per cell):
# each cell packs 8 subpixels into one U+28xx codepoint.
BRAILLE_BASE = 0x2800
BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)

# HalfBlock marker: 1 col x 2 rows per cell (top half / bottom half).
HALF_BLOCK_BITS = ((0x1,), (0x2,))
HALF_BLOCK_GLYPHS = (" ", "▀", "▄", "█")  # index = bit0(top) | bit1(bottom)

# Quadrant marker: 2 cols x 2 rows per cell. bit0=top-left, bit1=top-right,
# bit2=bottom-left, bit3=bottom-right.
QUADRANT_BITS = ((0x1, 0x2), (0x4, 0x8))
QUADRANT_GLYPHS = (
    " ",
    "▘",
    "▝",
    "▀",
    "▖",
    "▌",
    "▞",
    "▛",
    "▗",
    "▚",
    "▐",
    "▜",
    "▄",
    "▙",
    "▟",
    "█",
)
