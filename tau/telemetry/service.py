from __future__ import annotations

import asyncio
import atexit
import contextlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from posthog import Posthog

from tau.settings.paths import CONFIG_DIR_PATH
from tau.telemetry.types import InstallTelemetryEvent

POSTHOG_API_KEY = "phc_uxdCItyVTjXNU0sMPr97dq3tcz39scQNt3qjTYw5vLV"
POSTHOG_HOST = "https://us.i.posthog.com"
_REPORTED_VERSION_PATH = CONFIG_DIR_PATH / "telemetry-version"
_TIMEOUT = 5.0


async def _run_in_daemon_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a blocking call in a daemon thread so it never delays process exit.

    Unlike ``asyncio.to_thread``, which uses the loop's non-daemon default
    executor, a cancelled awaiter here returns immediately while the thread
    (being a daemon) is dropped by the interpreter on exit instead of being
    waited on.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def worker() -> None:
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - propagated to the awaiter
            if not loop.is_closed() and not future.cancelled():
                loop.call_soon_threadsafe(_set_exception, future, exc)
        else:
            if not loop.is_closed() and not future.cancelled():
                loop.call_soon_threadsafe(_set_result, future, result)

    threading.Thread(target=worker, daemon=True).start()
    return await future


def _set_result(future: asyncio.Future[Any], result: Any) -> None:
    if not future.cancelled():
        future.set_result(result)


def _set_exception(future: asyncio.Future[Any], exc: BaseException) -> None:
    if not future.cancelled():
        future.set_exception(exc)


async def report_install(
    version: str,
    *,
    host: str = POSTHOG_HOST,
    api_key: str = POSTHOG_API_KEY,
    reported_version_path: Path = _REPORTED_VERSION_PATH,
) -> None:
    """Report one anonymous install/update count for each installed Tau version.

    Sends a single PostHog ``tau`` event with only ``{"version": "..."}``
    as a property. Failures are ignored and retried at the next startup; the
    local marker is written only after a successful send.
    """
    try:
        if reported_version_path.read_text(encoding="utf-8").strip() == version:
            return
    except OSError:
        pass

    try:
        # sync_mode sends the event inline (no background thread) and, combined
        # with the client's default error-swallowing, returns None on failure.
        client = Posthog(api_key, host=host, sync_mode=True, timeout=_TIMEOUT)
        event = InstallTelemetryEvent(version=version)
        event_id = await _run_in_daemon_thread(
            client.capture,
            event.event_name,
            distinct_id="anonymous",
            properties=event.properties,
        )
        if event_id is None:
            return

        reported_version_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        reported_version_path.write_text(version, encoding="utf-8")
        reported_version_path.chmod(0o600)
    except Exception:
        return


_exception_capture_client: Posthog | None = None


def enable_exception_autocapture(
    *,
    host: str = POSTHOG_HOST,
    api_key: str = POSTHOG_API_KEY,
) -> None:
    """Install a process-wide handler that reports uncaught exceptions to PostHog.

    Replaces ``sys.excepthook``/``threading.excepthook`` for the life of the
    process, so this is idempotent: only the first call installs the client,
    since a second install would chain onto the first and double-report.
    """
    global _exception_capture_client
    if _exception_capture_client is not None:
        return
    client = Posthog(api_key, host=host, enable_exception_autocapture=True)
    # The consumer thread is already a daemon, but PostHog also registers an
    # atexit hook that blocks the main thread on `join()` until the queue
    # drains. Drop it so a crash can't hang process exit waiting to flush.
    with contextlib.suppress(Exception):
        atexit.unregister(client.join)
    _exception_capture_client = client
