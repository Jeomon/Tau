"""End-to-end tests for tau/remote over a real unix socket.

These deliberately use an actual socket rather than an in-memory double. The
behaviours worth guarding here — partial reads, a peer that stops reading, file
modes, a socket left behind by a crashed process — are precisely the ones a
fake transport would paper over.
"""

from __future__ import annotations

import asyncio
import socket
import stat
import tempfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tau.remote.client import RemoteClient
from tau.remote.framing import encode_frame
from tau.remote.protocol import PROTOCOL_VERSION, encode_message
from tau.remote.server import RemoteServer, SocketInUseError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not hasattr(socket, "AF_UNIX"), reason="unix sockets are unavailable on this platform"
    ),
]

TIMEOUT = 5.0


class _FakeHooks:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def register(self, name: str, handler):
        self.handlers.setdefault(name, []).append(handler)
        return lambda: self.handlers[name].remove(handler)

    async def fire(self, name: str, event: object) -> None:
        for handler in list(self.handlers.get(name, [])):
            await handler(event)


class _FakeRuntime:
    """Enough runtime for the greeting and for commands that fail early."""

    def __init__(self) -> None:
        self.hooks = _FakeHooks()
        self.agent = None


class _Event:
    """An event object, not a dict.

    ``wire.serialize_event`` reads ``__dict__``; a plain dict has none and
    would serialize as ``{"type": "dict"}``, testing nothing.
    """

    def __init__(self, type_name: str, **fields: object) -> None:
        self.type = type_name
        for key, value in fields.items():
            setattr(self, key, value)


@pytest.fixture
def socket_path() -> Iterator[Path]:
    """A socket path short enough for ``sun_path``.

    Not ``tmp_path``: pytest's per-test directories run well past the ~104-byte
    limit macOS imposes on unix socket paths, so binding there fails outright.
    """
    with tempfile.TemporaryDirectory(prefix="tau-rmt-") as directory:
        yield Path(directory) / "s" / "t.sock"


@asynccontextmanager
async def _serving(path: Path, runtime: _FakeRuntime | None = None, **kwargs):
    server = RemoteServer(runtime or _FakeRuntime(), path, **kwargs)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


@asynccontextmanager
async def _client(path: Path):
    client = RemoteClient(path)
    ready = await asyncio.wait_for(client.connect(), TIMEOUT)
    try:
        yield client, ready
    finally:
        await client.close()


class TestGreeting:
    async def test_client_receives_a_ready_greeting(self, socket_path):
        async with _serving(socket_path), _client(socket_path) as (_, ready):
            assert ready["type"] == "ready"
            assert ready["protocolVersion"] == PROTOCOL_VERSION

    async def test_greeting_carries_derived_capabilities(self, socket_path):
        """The same capability block stdio RPC announces, read from live state."""
        from tau.extensions.runtime import _INTERCEPTABLE_EVENTS

        async with _serving(socket_path), _client(socket_path) as (_, ready):
            assert ready["capabilities"]["toolCallBlocking"] == (
                "tool_call" in _INTERCEPTABLE_EVENTS
            )


class TestCommands:
    async def test_response_is_correlated_by_id(self, socket_path):
        async with _serving(socket_path), _client(socket_path) as (client, _):
            response = await client.request({"type": "nope"}, timeout=TIMEOUT)

            assert response["type"] == "response"
            assert response["success"] is False

    async def test_two_clients_do_not_see_each_others_responses(self, socket_path):
        """The point of the injectable sink, proven over a socket."""
        async with (
            _serving(socket_path),
            _client(socket_path) as (first, _),
            _client(socket_path) as (second, _),
        ):
            reply = await first.request({"type": "nope", "id": "only-mine"}, timeout=TIMEOUT)
            assert reply["id"] == "only-mine"

            with pytest.raises(asyncio.TimeoutError):
                await second.next_event(timeout=0.2)

    async def test_malformed_payload_is_answered_without_closing(self, socket_path):
        """A protocol error costs one reply, not the session."""
        async with _serving(socket_path), _client(socket_path) as (client, _):
            await client.request({"type": "nope"}, timeout=TIMEOUT)

            # Frames correctly, but is not a JSON object.
            assert client._writer is not None
            client._writer.write(encode_frame(b"[1,2,3]"))

            error = await client.next_event(timeout=TIMEOUT)
            assert error["success"] is False
            assert error["command"] == "parse"

            # Still usable afterwards.
            follow_up = await client.request({"type": "nope"}, timeout=TIMEOUT)
            assert follow_up["type"] == "response"


class TestBroadcast:
    async def test_every_client_receives_an_event(self, socket_path):
        async with (
            _serving(socket_path) as server,
            _client(socket_path) as (first, _),
            _client(socket_path) as (second, _),
        ):
            server.broadcast({"type": "custom_event", "value": 42})

            for client in (first, second):
                event = await client.next_event(timeout=TIMEOUT)
                assert event == {"type": "custom_event", "value": 42}

    async def test_runtime_events_reach_clients(self, socket_path):
        """Events come from the same hook set stdio RPC forwards."""
        from tau.modes.rpc.mode import _FORWARDED_EVENTS

        runtime = _FakeRuntime()
        async with _serving(socket_path, runtime), _client(socket_path) as (client, _):
            name = next(iter(_FORWARDED_EVENTS))
            await runtime.hooks.fire(name, _Event(name, detail="x"))

            event = await client.next_event(timeout=TIMEOUT)
            assert event["type"] == name
            assert event["detail"] == "x"

    async def test_events_are_not_mistaken_for_responses(self, socket_path):
        """An event arriving mid-request must not resolve the request."""
        async with _serving(socket_path) as server, _client(socket_path) as (client, _):
            server.broadcast({"type": "noise", "id": "c1"})
            response = await client.request({"type": "nope"}, timeout=TIMEOUT)

            assert response["type"] == "response"
            assert client.pending_events >= 1


class TestSlowClient:
    async def test_a_client_that_stops_reading_is_dropped(self, socket_path):
        """The property that keeps one observer from stalling the agent."""
        async with _serving(socket_path, max_queued=2) as server:
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.connect(str(socket_path))
            try:
                await asyncio.sleep(0.05)
                assert server.connection_count == 1

                # A synchronous burst: the pump never runs in between, so the
                # bounded queue is guaranteed to overflow.
                for index in range(100):
                    server.broadcast({"type": "flood", "index": index})

                assert server.connection_count == 0
            finally:
                raw.close()

    async def test_dropping_one_client_leaves_the_other_working(self, socket_path):
        """A dropped peer must not take the healthy one with it."""
        async with _serving(socket_path, max_queued=8) as server:
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.connect(str(socket_path))
            try:
                async with _client(socket_path) as (healthy, _):
                    await asyncio.sleep(0.05)
                    # Yield between sends so the healthy client's pump keeps
                    # draining; only the peer that never reads falls behind.
                    payload = "x" * 2048
                    for index in range(200):
                        server.broadcast({"type": "flood", "index": index, "pad": payload})
                        await asyncio.sleep(0)

                    response = await healthy.request({"type": "nope"}, timeout=TIMEOUT)
                    assert response["type"] == "response"
            finally:
                raw.close()


class TestSocketHygiene:
    async def test_socket_is_owner_only(self, socket_path):
        async with _serving(socket_path):
            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    async def test_socket_directory_is_owner_only(self, socket_path):
        async with _serving(socket_path):
            assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700

    async def test_a_stale_socket_is_replaced(self, socket_path):
        """What a crashed server leaves behind must not block a restart."""
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(socket_path))
        stale.close()  # closed without unlinking, exactly as a crash leaves it
        assert socket_path.exists()

        async with _serving(socket_path), _client(socket_path) as (_, ready):
            assert ready["type"] == "ready"

    async def test_a_regular_file_is_refused_rather_than_deleted(self, socket_path):
        """Tau must not delete a user's file to free up a path."""
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("precious")

        server = RemoteServer(_FakeRuntime(), socket_path)
        with pytest.raises(SocketInUseError, match="not a socket"):
            await server.start()

        assert socket_path.read_text() == "precious"

    async def test_a_live_socket_is_refused(self, socket_path):
        """Two servers on one path would silently split clients in half."""
        async with _serving(socket_path):
            second = RemoteServer(_FakeRuntime(), socket_path)

            with pytest.raises(SocketInUseError, match="already listening"):
                await second.start()

    async def test_close_removes_the_socket_file(self, socket_path):
        async with _serving(socket_path):
            assert socket_path.exists()

        assert not socket_path.exists()


class TestFraming:
    async def test_an_oversized_frame_closes_the_connection(self, socket_path):
        """Framing errors are fatal: the stream can no longer be trusted."""
        async with _serving(socket_path, max_frame_length=64) as server:
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.connect(str(socket_path))
            try:
                await asyncio.sleep(0.05)
                raw.sendall((10_000_000).to_bytes(4, "big"))  # header only
                await asyncio.sleep(0.1)

                assert server.connection_count == 0
            finally:
                raw.close()

    async def test_a_command_split_across_writes_still_arrives(self, socket_path):
        """The case a newline-delimited protocol gets wrong under load."""
        async with _serving(socket_path), _client(socket_path) as (client, _):
            frame = encode_message({"type": "nope", "id": "split"})
            assert client._writer is not None
            for index in range(len(frame)):
                client._writer.write(frame[index : index + 1])
                await asyncio.sleep(0)

            response = await client.request({"type": "nope", "id": "after"}, timeout=TIMEOUT)
            assert response["id"] == "after"
            # The split command was answered too, ahead of this one.
            assert client.pending_events >= 1
