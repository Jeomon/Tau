"""A unix-socket server exposing one running Tau session to several clients.

Scope, stated up front because it is the design's main constraint: **one server
serves one runtime.** A ``Runtime`` owns its session, and hosting several would
need a session-factory layer Tau does not have. So this is not a daemon that
lists and creates sessions on demand — it is multi-client access to the session
the server was handed.

That falls out of what Tau can actually do today rather than from what looks
impressive, and it still delivers the thing stdio RPC cannot: two clients
watching and driving one agent at once.

Three rules govern the wiring:

* **Responses are point-to-point, events are broadcast.** A response belongs to
  the client that asked; an event describes the session everyone is attached
  to. The dispatcher's injectable sink is what keeps the first true.
* **A slow client is dropped, never tolerated.** Its outbound queue is bounded,
  and a client that lets it fill is disconnected. The alternative — letting a
  queue grow, or awaiting a blocked write inside event delivery — makes any
  observer able to stall the agent everyone else is using.
* **Framing errors are fatal, protocol errors are not.** A bad frame means the
  stream is misaligned and unrecoverable; a bad message means one request was
  junk while the stream stayed in step.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import socket
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tau.modes.rpc import mode as rpc
from tau.remote.framing import DEFAULT_MAX_FRAME_LENGTH, FrameDecoder, FrameError
from tau.remote.protocol import PROTOCOL_VERSION, ProtocolError, decode_message, encode_message

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_log = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_QUEUED_MESSAGES", "RemoteServer", "SocketInUseError"]

#: Outbound messages a connection may fall behind by before it is dropped.
#: A streaming turn emits message_update rapidly, so this is generous enough to
#: absorb a client that pauses to render, and far below the point where a stuck
#: client's backlog becomes a memory problem for the server.
DEFAULT_MAX_QUEUED_MESSAGES = 1024

_SOCKET_DIR_MODE = 0o700
_SOCKET_MODE = 0o600

#: ``sockaddr_un.sun_path`` is a fixed-size field, and the kernel rejects
#: anything longer with a bare EINVAL/ENAMETOOLONG at bind. Checking first
#: turns that into a message naming the actual problem.
_MAX_SOCKET_PATH_BYTES = 104 if sys.platform == "darwin" else 108


class SocketInUseError(RuntimeError):
    """Another live server already holds the socket path."""


class _Connection:
    """One attached client, with its own outbound queue and writer task."""

    def __init__(self, writer: asyncio.StreamWriter, *, max_queued: int) -> None:
        self._writer = writer
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queued)
        self._pump: asyncio.Task | None = None
        self.tasks: set[asyncio.Task] = set()
        self.dropped = False

    def start(self) -> None:
        self._pump = asyncio.ensure_future(self._drain())

    def send(self, message: dict[str, Any]) -> None:
        """Queue a message. Never blocks, never raises at the call site.

        Called from event delivery and from the dispatcher's sink, both of
        which run on the agent's own task. Blocking here would let one client's
        socket buffer decide how fast the agent may run, so a full queue drops
        the connection instead.
        """
        if self.dropped:
            return
        try:
            self._queue.put_nowait(encode_message(message))
        except asyncio.QueueFull:
            self.dropped = True
            _log.warning(
                "remote: dropping client that fell %d messages behind", self._queue.qsize()
            )
            # Wake the pump so it observes `dropped` and tears the socket down.
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)

    async def _drain(self) -> None:
        try:
            while True:
                frame = await self._queue.get()
                if frame is None or self.dropped:
                    return
                self._writer.write(frame)
                await self._writer.drain()
        except (ConnectionError, OSError):
            return  # peer vanished; close() still runs from the serve path
        finally:
            self.dropped = True

    async def close(self) -> None:
        self.dropped = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        for task in list(self.tasks):
            task.cancel()
        if self._pump is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
        with contextlib.suppress(ConnectionError, OSError):
            self._writer.close()
            await self._writer.wait_closed()


class RemoteServer:
    """Serves one runtime over a unix socket."""

    def __init__(
        self,
        runtime: Runtime,
        socket_path: str | os.PathLike[str],
        *,
        max_frame_length: int = DEFAULT_MAX_FRAME_LENGTH,
        max_queued: int = DEFAULT_MAX_QUEUED_MESSAGES,
    ) -> None:
        self._runtime = runtime
        self._path = Path(socket_path)
        self._max_frame_length = max_frame_length
        self._max_queued = max_queued
        self._connections: set[_Connection] = set()
        self._server: asyncio.AbstractServer | None = None
        self._unsubscribes: list[Any] = []

    @property
    def socket_path(self) -> Path:
        return self._path

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._prepare_socket_path()
        self._server = await asyncio.start_unix_server(self._on_client, path=str(self._path))
        # Between bind and chmod the socket is briefly world-accessible, so the
        # 0o700 directory above is what actually gates access; this narrows the
        # window rather than being the only defence.
        os.chmod(self._path, _SOCKET_MODE)
        self._subscribe_events()
        _log.info("remote: listening on %s", self._path)

    def _prepare_socket_path(self) -> None:
        encoded = len(os.fsencode(self._path))
        if encoded >= _MAX_SOCKET_PATH_BYTES:
            # Checked before mkdir so a doomed path leaves nothing behind.
            raise ValueError(
                f"socket path is {encoded} bytes, over this platform's "
                f"{_MAX_SOCKET_PATH_BYTES}-byte limit: {self._path}"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_SOCKET_DIR_MODE)
        if not self._path.exists():
            return
        if not stat.S_ISSOCK(self._path.lstat().st_mode):
            # Refuse rather than clear the way. Everything this method removes
            # it removes unprompted, so it may only ever remove something it is
            # certain a server left behind — a regular file here is a user's,
            # and unlinking it would be Tau destroying data to start faster.
            raise SocketInUseError(
                f"{self._path} exists and is not a socket; refusing to replace it"
            )
        # A socket left by a crashed server is indistinguishable from a live
        # one by inspection, so probe it: a refused connection means nobody is
        # listening and the file is safe to replace.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(self._path))
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                raise
            self._path.unlink(missing_ok=True)
            return
        else:
            raise SocketInUseError(f"a server is already listening on {self._path}")
        finally:
            probe.close()

    async def close(self) -> None:
        for unsubscribe in self._unsubscribes:
            with contextlib.suppress(Exception):
                unsubscribe()
        self._unsubscribes.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        for connection in list(self._connections):
            await connection.close()
        self._connections.clear()
        self._path.unlink(missing_ok=True)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("start() must be called before serve_forever()")
        async with self._server:
            await self._server.serve_forever()

    # ── events ───────────────────────────────────────────────────────────────

    def _subscribe_events(self) -> None:
        """Forward the same event set stdio RPC forwards, to every client."""

        async def on_event(event: object) -> None:
            payload = rpc._serialize_event(event)
            event_type = payload.get("type")
            if event_type == "message_start":
                rpc._DELTAS.reset()
            elif event_type == "message_update":
                payload = rpc._DELTAS.annotate(payload, getattr(event, "message", None))
            self.broadcast(payload)

        hooks = self._runtime.hooks
        self._unsubscribes = [hooks.register(name, on_event) for name in rpc._FORWARDED_EVENTS]

    def broadcast(self, message: dict[str, Any]) -> None:
        for connection in list(self._connections):
            connection.send(message)
            if connection.dropped:
                self._connections.discard(connection)

    # ── connections ──────────────────────────────────────────────────────────

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = _Connection(writer, max_queued=self._max_queued)
        connection.start()
        self._connections.add(connection)
        try:
            connection.send(self._ready_message())
            await self._read_loop(reader, connection)
        finally:
            self._connections.discard(connection)
            await connection.close()

    def _ready_message(self) -> dict[str, Any]:
        """Greet a new client with the same shape stdio RPC announces.

        Derived from the live runtime for the same reason ``_capabilities`` is:
        a greeting that restates constants is right until the day it is not.
        """
        return {
            "type": "ready",
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": rpc._capabilities(self._runtime),
            "attached": len(self._connections),
        }

    async def _read_loop(self, reader: asyncio.StreamReader, connection: _Connection) -> None:
        decoder = FrameDecoder(max_frame_length=self._max_frame_length)
        while not connection.dropped:
            try:
                chunk = await reader.read(64 * 1024)
            except (ConnectionError, OSError):
                return
            if not chunk:
                return
            try:
                frames = list(decoder.feed(chunk))
            except FrameError as exc:
                # Unrecoverable: the stream is no longer aligned.
                _log.warning("remote: framing error, closing connection: %s", exc)
                connection.send({"type": "error", "error": str(exc), "fatal": True})
                return
            for payload in frames:
                self._dispatch(payload, connection)

    def _dispatch(self, payload: bytes, connection: _Connection) -> None:
        try:
            command = decode_message(payload)
        except ProtocolError as exc:
            # Recoverable: reply and keep the connection.
            connection.send(
                {
                    "type": "response",
                    "command": "parse",
                    "success": False,
                    "error": f"Failed to parse command: {exc}",
                }
            )
            return
        # Concurrent, like the stdio loop: a client must be able to interrupt
        # its own in-flight prompt, which a sequential loop would deadlock.
        task = asyncio.ensure_future(
            rpc._handle_command(command, self._runtime, rpc._UI_PENDING, write=connection.send)
        )
        connection.tasks.add(task)
        task.add_done_callback(connection.tasks.discard)
