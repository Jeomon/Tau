from __future__ import annotations

from typing import Any


def extract_path(args: dict[str, Any]) -> str | None:
    """Return the first file-path-like argument value, if any."""
    for key in ("path", "file_path", "filePath", "file"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return None
