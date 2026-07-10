"""Shared formatting helpers for human-readable numbers.

``format_number`` lives in ``tau.tui.utils`` — the ``tau.tui`` package is a
standalone toolkit that must not import the application layer, but the
reverse is fine, so its canonical home is there and it's re-exported here for
the many application-layer callers that already import it from this module.
"""

from __future__ import annotations

from tau.tui.utils import format_number

__all__ = ["format_number", "human_size"]

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(n: int) -> str:
    """Format a byte count using binary (1024-based) B/KB/MB/GB/TB units."""
    value = float(n)
    for unit in _SIZE_UNITS[:-1]:
        if value < 1024:
            return f"{n}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}{_SIZE_UNITS[-1]}"
