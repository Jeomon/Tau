"""Animated recording caret.

While the mic is live we replace the text cursor with a coloured block that
pulses in height (using the eighth-block glyphs ``▁``–``█``) and glows brighter
as it grows. Recording and transcribing use different colour ramps so the two
phases are visually distinct; on release the caret reverts to the normal block.
"""

from __future__ import annotations

from collections.abc import Callable

_RESET = "\x1b[0m"

# Eighth-block glyphs, low → full → low, so the cell appears to grow and shrink.
_HEIGHTS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"]
_FILL = "▁▂▃▄▅▆▇█"  # index → fill level 0..7

# 256-colour ramps (dim → bright) keyed to the block's height for a glow effect.
RECORDING = [52, 88, 124, 160, 196, 197, 198, 199]  # deep → hot red
TRANSCRIBING = [23, 30, 37, 44, 45, 51, 87, 123]  # deep → bright cyan

FRAME_INTERVAL = 0.08  # seconds per frame


def frame(ramp: list[int], i: int) -> Callable[[str], str]:
    """Return a ``cursor_cell``-compatible renderer for animation frame ``i``.

    The returned callable ignores the underlying character and draws the
    coloured block for this frame, so it can be assigned to
    ``TextInput.cursor_cell``.
    """
    glyph = _HEIGHTS[i % len(_HEIGHTS)]
    level = _FILL.find(glyph)
    idx = ramp[round(level / (len(_FILL) - 1) * (len(ramp) - 1))]
    cell = f"\x1b[38;5;{idx}m{glyph}{_RESET}"
    return lambda _ch=" ": cell
