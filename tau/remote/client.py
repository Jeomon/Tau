"""A client for a :class:`~tau.remote.server.RemoteServer`.

The asymmetry with the server is deliberate. A server must never let one client
affect another, so it drops anyone who falls behind. A client has exactly one
peer and nothing to protect it from, so it buffers instead: events arrive on a
queue the caller drains at its own pace, and falling behind costs memory rather
than the connection.

Commands are correlated by ``id``. The server answers each with a single
``response`` and may interleave any number of events, so a caller that simply
read the next message would routinely mistake an event for its answer. Sending
through :meth:`RemoteClient.request` awaits the matching id and lets everything
else fall through to the event stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from itertools import count
from typing import Any

from tau.remote.framing import DEFAULT_MAX_FRAME_LENGTH, FrameDecoder, FrameError
from tau.remote.protocol import ProtocolError, decode_message, encode_message

__all__ = ["RemoteClient", "RemoteDisconnected"]


class RemoteDisconnected(RuntimeError):
    """The connection closed, either normally or mid-request."""


class RemoteClient:
    """Connects to a Tau remote server over a unix socket."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        max_frame_length: int = DEFAULT_MAX_FRAME_LENGTH,
    ) -> None:
        self._path = str(socket_path)
        self._max_frame_length = max_frame_length
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._ids = count(1)
        self._closed = False
        self._last_revision = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Connect and return the server's ``ready`` greeting.

        Returning it rather than swallowing it means the version and capability
        handshake is impossible to skip by accident — a caller holds the
        server's terms before it can send anything.
        """
        self._reader, self._writer = await asyncio.open_unix_connection(self._path)
        self._pump = asyncio.ensure_future(self._read_loop())
        ready = await self.next_event()
        if ready.get("type") != "ready":
            raise RemoteDisconnected(f"expected a ready greeting, got {ready.get('type')!r}")
        return ready

    async def close(self) -> None:
        self._closed = True
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            with contextlib.suppress(ConnectionError, OSError):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None
        self._fail_pending(RemoteDisconnected("connection closed"))

    async def __aenter__(self) -> RemoteClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── sending ──────────────────────────────────────────────────────────────

    def send(self, message: dict[str, Any]) -> None:
        """Send one message without awaiting a reply."""
        if self._writer is None or self._closed:
            raise RemoteDisconnected("not connected")
        self._writer.write(encode_message(message))

    async def request(self, command: dict[str, Any], *, timeout: float | None = None) -> dict:
        """Send a command and await its response, ignoring interleaved events."""
        message = dict(command)
        command_id = str(message.get("id") or f"c{next(self._ids)}")
        message["id"] = command_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            self.send(message)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(command_id, None)

    # ── receiving ────────────────────────────────────────────────────────────

    async def next_event(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Await the next non-response message."""
        return await asyncio.wait_for(self._events.get(), timeout)

    @property
    def pending_events(self) -> int:
        return self._events.qsize()

    @property
    def last_revision(self) -> int:
        """The revision of the most recent settled event seen.

        Survives ``close()`` on purpose: it is the cursor a reconnecting
        client hands back to the server, so a client that drops the connection
        and builds a new one can still say where it got to.
        """
        return self._last_revision

    async def resume(
        self, *, since: int | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """Ask the server to replay settled events missed since ``since``.

        Defaults to this client's own cursor. Returns the ``resumed`` reply;
        check ``replayed`` before trusting the stream to be complete, because
        False means the requested point has already been evicted and the
        session must be refetched with ``get_messages``/``get_state`` instead.

        The replayed events arrive after this returns, on the normal event
        queue, so ordinary consumption picks them up.
        """
        message = {
            "type": "resume",
            "since": self._last_revision if since is None else since,
        }
        return await self.request(message, timeout=timeout)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        decoder = FrameDecoder(max_frame_length=self._max_frame_length)
        try:
            while True:
                chunk = await self._reader.read(64 * 1024)
                if not chunk:
                    self._fail_pending(RemoteDisconnected("server closed the connection"))
                    return
                for payload in decoder.feed(chunk):
                    self._deliver(payload)
        except (ConnectionError, OSError, FrameError) as exc:
            self._fail_pending(RemoteDisconnected(str(exc)))

    def _deliver(self, payload: bytes) -> None:
        try:
            message = decode_message(payload)
        except ProtocolError:
            # The server framed it correctly but sent something unreadable.
            # Dropping one message is better than tearing down a live session.
            return
        revision = message.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            # Tracked from the message itself, so a replayed event advances the
            # cursor exactly as the original would have. Assigned rather than
            # maxed: replay arrives in order, and clamping would hide a server
            # that restarted and began renumbering.
            self._last_revision = revision
        message_id = message.get("id")
        # `resumed` is correlated like a response: it answers a specific
        # request, and letting it fall through to the event queue would leave
        # resume() waiting on a message it had already been handed.
        if message.get("type") in ("response", "resumed") and isinstance(message_id, str):
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)
                return
        self._events.put_nowait(message)

    def _fail_pending(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
