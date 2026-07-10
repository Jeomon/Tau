"""Shared formatting helpers for human-readable numbers."""

from __future__ import annotations

_SUFFIXES = ("", "K", "M", "B", "T")


def format_number(num: int | float) -> str:
    """Format a count compactly using K/M/B/T suffixes.

    Values below 1,000 are rendered as plain integers. Larger values are
    divided by 1,000 repeatedly and shown with one decimal place, dropping
    the decimal when it's redundant (e.g. ``1_200`` -> ``"1.2K"``,
    ``2_000_000`` -> ``"2M"``).
    """
    if num < 1_000:
        return str(int(num))

    value = float(num)
    i = 0
    while value >= 1_000 and i < len(_SUFFIXES) - 1:
        value /= 1_000
        i += 1
    # Rounding to one decimal can push the display value up to the next
    # tier's boundary (e.g. 999_999 -> 999.999K, which rounds to "1000.0K"
    # instead of "1M") — bump the tier once more in that case.
    if round(value, 1) >= 1_000 and i < len(_SUFFIXES) - 1:
        value /= 1_000
        i += 1

    if round(value, 1).is_integer():
        return f"{int(round(value))}{_SUFFIXES[i]}"
    return f"{value:.1f}{_SUFFIXES[i]}"


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(n: int) -> str:
    """Format a byte count using binary (1024-based) B/KB/MB/GB/TB units."""
    value = float(n)
    for unit in _SIZE_UNITS[:-1]:
        if value < 1024:
            return f"{n}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}{_SIZE_UNITS[-1]}"
