"""Resolve secret references (API keys, tokens, proxy creds) to their values.

A "secret reference" is a string that may be one of three forms:

  - ``"literal-value"``  → returned unchanged
  - ``"$ENV_VAR"``       → read from ``os.environ`` (empty string if unset)
  - ``"!shell command"`` → run in a shell; its trimmed stdout is the value

Resolution is **memoized**: a ``$VAR`` / ``!command`` reference is read/executed
only the first time it is seen, and the resolved value is kept in memory for the
life of the process. Subsequent lookups return the cached value, so callers on a
hot path (per-request headers, proxy config) never re-run the command.

Failed resolutions (empty result) are *not* cached, so fixing the environment or
the command and reloading re-resolves rather than being stuck on the empty value.
"""

from __future__ import annotations

import os
import subprocess

_cache: dict[str, str] = {}
_MAX_CACHE = 256


def resolve_secret(value: str | None) -> str:
    """Resolve a single secret reference to its value (memoized). See module docs."""
    if not value:
        return ""
    cached = _cache.get(value)
    if cached is not None:
        return cached

    if value.startswith("$"):
        resolved = os.environ.get(value[1:], "")
    elif value.startswith("!"):
        resolved = subprocess.run(
            value[1:], shell=True, capture_output=True, text=True
        ).stdout.strip()
    else:
        resolved = value

    if resolved:  # don't cache failures — allow a later retry after a fix
        if len(_cache) >= _MAX_CACHE:
            del _cache[next(iter(_cache))]  # evict oldest (insertion-order)
        _cache[value] = resolved
    return resolved


def resolve_secrets(values: dict[str, str] | None) -> dict[str, str]:
    """Resolve every string value in a mapping (e.g. a header dict)."""
    if not values:
        return {}
    return {k: resolve_secret(v) if isinstance(v, str) else v for k, v in values.items()}


def clear_cache() -> None:
    """Forget all memoized resolutions (e.g. for tests or a hard reload)."""
    _cache.clear()
