"""Run loop for ``--mode remote``.

Starts a :class:`~tau.remote.server.RemoteServer` around the runtime and waits.
Unlike RPC mode there is no stdin to read and no protocol on stdout, so stdout
stays human-readable: it prints where it is listening and then gets out of the
way. That is the whole difference in this file — everything the clients
actually talk to lives in ``tau.remote``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tau.modes.rpc.mode import install_extension_ui_bridge
from tau.modes.signals import exit_on_signal
from tau.remote.server import RemoteServer

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_log = logging.getLogger(__name__)

__all__ = ["run_remote_mode"]


def resolve_socket_path(runtime: Runtime, socket_path: str | None) -> Path:
    """Pick the socket path: the flag if given, else one named for the session."""
    if socket_path:
        return Path(socket_path).expanduser()
    from tau.settings.paths import get_remote_socket_path

    session_id = runtime.session_manager.session_id or "session"
    return get_remote_socket_path(session_id)


async def run_remote_mode(runtime: Runtime, socket_path: str | None = None) -> None:
    """Serve ``runtime`` over a unix socket until interrupted."""
    path = resolve_socket_path(runtime, socket_path)
    server = RemoteServer(runtime, path)
    await server.start()

    # Point extension dialogs at the attached clients. Without this an
    # extension calling ctx.select() would wait on an answer that no one could
    # send, since the default bridge writes to a stdout nothing is reading.
    install_extension_ui_bridge(runtime, write=server.broadcast)

    shutdown = asyncio.Event()

    def _request_shutdown() -> None:
        shutdown.set()

    def _on_signal() -> None:
        agent = runtime.agent
        if agent is not None:
            cancel_fn = getattr(agent, "cancel", None) or getattr(agent, "abort", None)
            if callable(cancel_fn):
                cancel_fn()
        _request_shutdown()

    # An extension calling ctx.shutdown() unwinds through here, so the socket
    # is removed on the way out rather than left for the next run to probe.
    runtime.set_shutdown_handler(_request_shutdown)

    print(f"tau: serving on {path}", flush=True)
    print("tau: press Ctrl-C to stop", flush=True)

    serving = asyncio.ensure_future(server.serve_forever())
    with exit_on_signal(_on_signal):
        loop = asyncio.get_running_loop()
        import signal as _signal

        sigint = getattr(_signal, "SIGINT", None)
        if sigint is not None:
            with contextlib.suppress(NotImplementedError, OSError):
                loop.add_signal_handler(sigint, _on_signal)
        try:
            await shutdown.wait()
        finally:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await serving
            await server.close()
            _log.info("remote: stopped serving %s", path)
