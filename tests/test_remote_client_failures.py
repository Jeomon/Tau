"""How RemoteClient behaves when the server misbehaves or goes away.

The happy path is covered in test_remote_server.py against a real
RemoteServer. These are the paths that only appear when the other end dies
mid-request, greets wrongly, or sends something unreadable — which a
well-behaved server never does, so they need a server that misbehaves on
purpose.

The distinction under test throughout: a *transport* failure must fail every
caller waiting on it rather than leave them hanging forever, while a single
unreadable *message* must not take down a working session.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import tempfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tau.remote.client import RemoteClient, RemoteDisconnected
from tau.remote.framing import encode_frame
from tau.remote.protocol import encode_message

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not hasattr(socket, "AF_UNIX"), reason="unix sockets are unavailable on this platform"
    ),
]

TIMEOUT = 5.0
_READY = {"type": "ready", "protocolVersion": 1, "capabilities": {}}


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="tau-cf-") as directory:
        yield Path(directory) / "t.sock"


class _Peer:
    """The server side of one connection, driven byte by byte from a test."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    def send(self, message: dict) -> None:
        self.writer.write(encode_message(message))

    def send_raw(self, payload: bytes) -> None:
        """Send a well-formed frame carrying an arbitrary payload."""
        self.writer.write(encode_frame(payload))

    async def drop(self) -> None:
        """Close abruptly, as a dying server would."""
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


@asynccontextmanager
async def _misbehaving_server(path: Path, greeting: dict | None = _READY):
    """A raw server that greets (or doesn't) and then does nothing on its own."""
    peers: list[_Peer] = []
    connected = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = _Peer(reader, writer)
        peers.append(peer)
        if greeting is not None:
            peer.send(greeting)
        connected.set()

    path.parent.mkdir(parents=True, exist_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        yield server, peers, connected
    finally:
        # Close the peers before the server. Since Python 3.12 wait_closed()
        # also waits for open connections, so a server holding a live peer
        # never finishes closing — RemoteServer.close() disconnects its
        # clients first for the same reason.
        for peer in peers:
            await peer.drop()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


class TestGreeting:
    async def test_a_non_ready_greeting_is_refused(self, socket_path):
        """Skipping the handshake must be loud, not silently tolerated."""
        async with _misbehaving_server(socket_path, greeting={"type": "hello"}):
            client = RemoteClient(socket_path)

            with pytest.raises(RemoteDisconnected, match="ready greeting"):
                await asyncio.wait_for(client.connect(), TIMEOUT)

            await client.close()


class TestContextManager:
    async def test_async_with_connects_and_closes(self, socket_path):
        async with _misbehaving_server(socket_path):
            async with RemoteClient(socket_path) as client:
                assert client._writer is not None

            assert client._writer is None, "closing must release the transport"


class TestSendWhenUnavailable:
    async def test_send_before_connect_is_refused(self, socket_path):
        client = RemoteClient(socket_path)

        with pytest.raises(RemoteDisconnected, match="not connected"):
            client.send({"type": "prompt"})

    async def test_send_after_close_is_refused(self, socket_path):
        async with _misbehaving_server(socket_path):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await client.close()

            with pytest.raises(RemoteDisconnected):
                client.send({"type": "prompt"})


class TestTransportFailure:
    async def test_a_pending_request_fails_when_the_server_closes(self, socket_path):
        """The property that matters: callers are failed, not left hanging."""
        async with _misbehaving_server(socket_path) as (_server, peers, connected):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await asyncio.wait_for(connected.wait(), TIMEOUT)

            pending = asyncio.ensure_future(client.request({"type": "get_state"}))
            await asyncio.sleep(0.05)  # let it reach the server unanswered
            await peers[0].drop()

            with pytest.raises(RemoteDisconnected):
                await asyncio.wait_for(pending, TIMEOUT)

            await client.close()

    async def test_every_pending_request_is_failed(self, socket_path):
        """_fail_pending must resolve all of them, not just the first."""
        async with _misbehaving_server(socket_path) as (_server, peers, connected):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await asyncio.wait_for(connected.wait(), TIMEOUT)

            first = asyncio.ensure_future(client.request({"type": "a"}))
            second = asyncio.ensure_future(client.request({"type": "b"}))
            await asyncio.sleep(0.05)
            await peers[0].drop()

            for pending in (first, second):
                with pytest.raises(RemoteDisconnected):
                    await asyncio.wait_for(pending, TIMEOUT)

            await client.close()

    async def test_closing_the_client_fails_its_pending_requests(self, socket_path):
        """A caller that closes mid-flight still has to be released."""
        async with _misbehaving_server(socket_path) as (_server, _peers, connected):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await asyncio.wait_for(connected.wait(), TIMEOUT)

            pending = asyncio.ensure_future(client.request({"type": "get_state"}))
            await asyncio.sleep(0.05)
            await client.close()

            with pytest.raises(RemoteDisconnected, match="connection closed"):
                await asyncio.wait_for(pending, TIMEOUT)


class TestUnreadableMessages:
    async def test_an_undecodable_message_is_dropped_not_fatal(self, socket_path):
        """One bad message must not tear down a live session."""
        async with _misbehaving_server(socket_path) as (_server, peers, connected):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await asyncio.wait_for(connected.wait(), TIMEOUT)

            peers[0].send_raw(b"{not json at all")
            peers[0].send({"type": "event_after_the_bad_one"})

            event = await client.next_event(timeout=TIMEOUT)
            assert event["type"] == "event_after_the_bad_one"

            await client.close()

    async def test_a_response_without_a_known_id_becomes_an_event(self, socket_path):
        """An unmatched response is surfaced, not silently discarded."""
        async with _misbehaving_server(socket_path) as (_server, peers, connected):
            client = RemoteClient(socket_path)
            await asyncio.wait_for(client.connect(), TIMEOUT)
            await asyncio.wait_for(connected.wait(), TIMEOUT)

            peers[0].send({"type": "response", "id": "never-asked", "success": True})

            event = await client.next_event(timeout=TIMEOUT)
            assert event["id"] == "never-asked"

            await client.close()
