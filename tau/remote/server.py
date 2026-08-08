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
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tau.modes.rpc import mode as rpc
from tau.remote.framing import DEFAULT_MAX_FRAME_LENGTH, FrameDecoder, FrameError
from tau.remote.protocol import PROTOCOL_VERSION, ProtocolError, decode_message, encode_message

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_QUEUED_MESSAGES",
    "RemoteServer",
    "SocketInUseError",
    "sweep_stale_sockets",
]

#: Outbound messages a connection may fall behind by before it is dropped.
#: A streaming turn emits message_update rapidly, so this is generous enough to
#: absorb a client that pauses to render, and far below the point where a stuck
#: client's backlog becomes a memory problem for the server.
DEFAULT_MAX_QUEUED_MESSAGES = 1024

#: Settled events retained for replay to a reconnecting client, and the total
#: bytes they may occupy. Both bounds apply: a few enormous events must not
#: consume the memory a long tail of small ones would have used.
DEFAULT_REPLAY_EVENTS = 256
DEFAULT_REPLAY_BYTES = 4 * 1024 * 1024

#: Event types excluded from the replay buffer. A streaming turn emits
#: message_update continuously and each is superseded by the message_end that
#: follows, so replaying them costs memory to deliver something the client
#: would immediately overwrite.
_EPHEMERAL_EVENTS = frozenset({"message_update"})

_SOCKET_DIR_MODE = 0o700
_SOCKET_MODE = 0o600

#: ``sockaddr_un.sun_path`` is a fixed-size field, and the kernel rejects
#: anything longer with a bare EINVAL/ENAMETOOLONG at bind. Checking first
#: turns that into a message naming the actual problem.
_MAX_SOCKET_PATH_BYTES = 104 if sys.platform == "darwin" else 108


def _is_listening(path: Path) -> bool:
    """Whether a live server holds ``path``.

    A socket file says nothing about whether anyone is behind it — the only way
    to tell a live server from the corpse of a crashed one is to try.

    Only "refused" and "not there" count as absence. Any other error means the
    question was not answered — a permission problem, say — and is raised
    rather than read as a no, because every caller treats a no as licence to
    delete the file.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except OSError as exc:
        if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
            raise
        return False
    else:
        return True
    finally:
        probe.close()


def sweep_stale_sockets(directory: Path, *, keep: Path | None = None) -> list[Path]:
    """Remove socket files in ``directory`` that nobody is listening on.

    Socket paths are named for their session and so are never reused, which
    means the replace-on-bind check never revisits one. A server killed without
    unwinding therefore leaves a file that nothing would ever clean up, and
    those accumulate in the user's config directory indefinitely.

    Only sockets are considered, and only ones that refuse a connection, so a
    running server is never disturbed and a regular file is never touched.
    ``keep`` exempts the path the caller is about to bind — that one belongs to
    ``RemoteServer.start``, which reports a live holder as an error rather than
    quietly removing it.

    Returns the paths removed. Failures are logged and skipped: a tidy-up that
    cannot proceed must not stop a server from starting.
    """
    removed: list[Path] = []
    try:
        entries = sorted(directory.glob("*.sock"))
    except OSError:  # unreadable or missing directory — nothing to tidy
        return removed
    for entry in entries:
        if keep is not None and entry == keep:
            continue
        try:
            if not stat.S_ISSOCK(entry.lstat().st_mode) or _is_listening(entry):
                continue
            entry.unlink()
        except OSError as exc:
            _log.debug("remote: could not sweep %s: %s", entry, exc)
            continue
        removed.append(entry)
    if removed:
        _log.info("remote: removed %d stale socket(s)", len(removed))
    return removed


class SocketInUseError(RuntimeError):
    """Another live server already holds the socket path."""


class _ReplayBuffer:
    """The recent settled events, so a reconnecting client can catch up.

    Revisions are assigned **only** to events that enter the buffer. That is
    what makes a replay exact rather than approximate: if ephemeral events also
    consumed numbers, a client asking for everything after revision 400 could
    be told it was fully caught up while the events numbered 401-410 had never
    been retained. A gap the client cannot see is worse than no replay at all,
    so unbuffered events are simply not numbered.

    Both bounds apply. Capping only the count lets a handful of very large
    events hold megabytes; capping only the bytes lets a flood of tiny ones
    grow the deque without limit.
    """

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_REPLAY_EVENTS,
        max_bytes: int = DEFAULT_REPLAY_BYTES,
    ) -> None:
        if max_events < 0 or max_bytes < 0:
            raise ValueError("replay bounds must not be negative")
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._events: deque[tuple[int, dict[str, Any], int]] = deque()
        self._bytes = 0
        self._revision = 0

    @property
    def latest_revision(self) -> int:
        """The highest revision issued, whether or not it is still retained."""
        return self._revision

    @property
    def oldest_revision(self) -> int | None:
        """The oldest revision still replayable, or None when empty."""
        return self._events[0][0] if self._events else None

    def __len__(self) -> int:
        return len(self._events)

    def add(self, message: dict[str, Any]) -> dict[str, Any]:
        """Stamp a settled event with its revision, retain it, and return it.

        The stamped copy is what gets both buffered and sent, so a replayed
        event carries the same ``revision`` it did the first time. Buffering
        the unstamped original would hand a reconnecting client events it
        could not number, leaving it unable to say where it got to.
        """
        self._revision += 1
        stamped = {**message, "revision": self._revision}
        size = len(encode_message(stamped))
        self._events.append((self._revision, stamped, size))
        self._bytes += size
        while self._events and (
            len(self._events) > self._max_events or self._bytes > self._max_bytes
        ):
            _, _, evicted = self._events.popleft()
            self._bytes -= evicted
        return stamped

    def since(self, revision: int) -> tuple[list[dict[str, Any]], bool]:
        """Return the events after ``revision`` and whether they are complete.

        The boolean is the honest part of the contract: False means the
        requested point has already been evicted, so the caller must resync
        from the session rather than assume the returned list is the whole
        story.
        """
        if revision < 0 or revision > self._revision:
            # Ahead of the server: a different server, or a restarted one.
            return [], False
        if revision == self._revision:
            return [], True  # already current, nothing missed
        oldest = self.oldest_revision
        if oldest is None or revision + 1 < oldest:
            return [], False
        return [message for number, message, _ in self._events if number > revision], True


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
        max_replay_events: int = DEFAULT_REPLAY_EVENTS,
        max_replay_bytes: int = DEFAULT_REPLAY_BYTES,
    ) -> None:
        self._runtime = runtime
        self._path = Path(socket_path)
        self._max_frame_length = max_frame_length
        self._max_queued = max_queued
        self._connections: set[_Connection] = set()
        self._server: asyncio.AbstractServer | None = None
        self._unsubscribes: list[Any] = []
        self._replay = _ReplayBuffer(max_events=max_replay_events, max_bytes=max_replay_bytes)

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
        if _is_listening(self._path):
            raise SocketInUseError(f"a server is already listening on {self._path}")
        self._path.unlink(missing_ok=True)

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
        """Send an event to every attached client, retaining it for replay.

        Settled events are numbered and buffered; ephemeral ones go out
        unnumbered, since a client that reconnects mid-stream wants the
        message that settled, not the deltas that built it.
        """
        if message.get("type") not in _EPHEMERAL_EVENTS:
            message = self._replay.add(message)
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
            # Where the event stream has got to, so a client that reconnects
            # knows what to ask for — and, on a first connection, what its
            # starting point is.
            "revision": self._replay.latest_revision,
            "oldestRevision": self._replay.oldest_revision,
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
        if command.get("type") == "resume":
            # Answered here rather than by the runtime dispatcher: replay is a
            # property of this connection's transport, not of the session, and
            # the dispatcher would rightly call it an unknown command.
            self._resume(command, connection)
            return
        # Concurrent, like the stdio loop: a client must be able to interrupt
        # its own in-flight prompt, which a sequential loop would deadlock.
        task = asyncio.ensure_future(
            rpc._handle_command(command, self._runtime, rpc._UI_PENDING, write=connection.send)
        )
        connection.tasks.add(task)
        task.add_done_callback(connection.tasks.discard)

    def _resume(self, command: dict[str, Any], connection: _Connection) -> None:
        """Replay settled events after ``since`` for a reconnecting client.

        The ``replayed`` flag carries the whole contract. True means every
        settled event after ``since`` follows; False means the requested point
        has already been evicted (or came from a different server) and the
        client must resync from the session with ``get_messages``/``get_state``
        instead. Saying so is the point — a partial replay that looks complete
        would leave a client confidently out of date.
        """
        since = command.get("since")
        # bool is an int in Python, and `since: true` is a client bug worth
        # naming rather than quietly treating as revision 1.
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            connection.send(
                {
                    "type": "resumed",
                    "id": command.get("id"),
                    "replayed": False,
                    "reason": f"'since' must be a non-negative integer, got {since!r}",
                    "revision": self._replay.latest_revision,
                }
            )
            return

        events, replayed = self._replay.since(since)
        reply: dict[str, Any] = {
            "type": "resumed",
            "id": command.get("id"),
            "replayed": replayed,
            "count": len(events),
            "revision": self._replay.latest_revision,
            "oldestRevision": self._replay.oldest_revision,
        }
        if not replayed:
            reply["reason"] = (
                f"revision {since} is no longer buffered; resync from the session"
                if since <= self._replay.latest_revision
                else f"revision {since} is ahead of this server (at {self._replay.latest_revision})"
            )
        connection.send(reply)
        # After the header, so the client knows how many to expect before they
        # start arriving.
        for event in events:
            connection.send(event)
