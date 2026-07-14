from __future__ import annotations

import asyncio
import contextlib

_TIMEOUT = 5.0


def _pypi_url() -> str:
    """PyPI JSON endpoint for this app's distribution (keyed on the app name)."""
    from tau.settings.paths import get_package_name

    return f"https://pypi.org/pypi/{get_package_name()}/json"


def _is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer release than ``current``.

    Uses PEP 440 comparison (handles rc/dev/post suffixes correctly). Falls back
    to a naive dotted-int compare if packaging is unavailable or a version string
    is non-standard; on total failure, conservatively reports "not newer".
    """
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            pass
    except ImportError:
        pass

    def _parse(v: str) -> tuple[int, ...]:
        parts = []
        for x in v.strip().split("."):
            with contextlib.suppress(ValueError):
                parts.append(int(x))
        return tuple(parts)

    try:
        return _parse(latest) > _parse(current)
    except Exception:
        return False


def _fetch_latest_version_sync() -> str:
    """Blocking GET against PyPI; run off the event loop (see ``check_for_new_version``)."""
    import httpx

    from tau.utils.ssl_context import get_shared_ssl_context

    with httpx.Client(timeout=_TIMEOUT, verify=get_shared_ssl_context()) as client:
        resp = client.get(_pypi_url(), headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        return data.get("info", {}).get("version", "")


async def check_for_new_version(current_version: str) -> str | None:
    """Return the latest PyPI version string if newer than current, else None.

    Runs on a worker thread: the first httpx client built anywhere in the
    process constructs an SSL context (see ``tau.utils.ssl_context``), which on
    Windows can take several hundred ms of synchronous CPU work. Doing that
    inline on the event loop — even though this whole check is itself a
    backgrounded, unawaited task — would still stall the just-launched TUI's
    render loop for that duration, since both share the same thread.
    """
    try:
        latest = await asyncio.to_thread(_fetch_latest_version_sync)
        if latest and _is_newer(latest, current_version):
            return latest
    except Exception:
        return None
    return None
