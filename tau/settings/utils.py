from __future__ import annotations

from typing import Any


def set_nested(d: dict, key: str, value: Any) -> None:
    """Set ``value`` at a dot-separated ``key`` path inside dict ``d``,
    creating intermediate dicts.
    """
    parts = key.split(".", 1)
    if len(parts) == 1:
        d[key] = value
    else:
        head, rest = parts
        if not isinstance(d.get(head), dict):
            d[head] = {}
        set_nested(d[head], rest, value)


def coerce_enum(enum_cls: type, value: Any) -> Any:
    """Coerce a raw value into ``enum_cls``; return None on failure (treated as unset)."""
    if value is None or isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return None
