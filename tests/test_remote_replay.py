"""Reconnect replay: revision stamps and the bounded settled-event buffer.

The contract these guard is narrow and worth stating plainly. A replay either
delivers *every* settled event after the requested revision, or says it could
not. There is no third outcome, because a partial replay that reports success
leaves a client confidently out of date — the failure mode replay exists to
remove.

That is why revisions are assigned only to buffered events. If streaming
deltas also consumed numbers, a client asking for everything after revision 400
could be told it was caught up while 401-410 had never been retained.
"""

from __future__ import annotations

import asyncio
import socket
import tempfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.remote.client import RemoteClient
from tau.remote.server import RemoteServer, _ReplayBuffer

# Only skipif is module-wide: TestReplayBuffer is synchronous, and marking it
# asyncio would warn on every test in it.
pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="unix sockets are unavailable on this platform"
)

TIMEOUT = 5.0


class _FakeHooks:
    def register(self, name: str, handler):
        return lambda: None


class _FakeRuntime:
    def __init__(self) -> None:
        self.hooks = _FakeHooks()
        self.agent = None
        self.session_manager = SimpleNamespace(session_id="replay-test")


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="tau-rp-") as directory:
        yield Path(directory) / "t.sock"


@asynccontextmanager
async def _serving(path: Path, **kwargs):
    server = RemoteServer(_FakeRuntime(), path, **kwargs)
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


async def _drain(client: RemoteClient, count: int) -> list[dict]:
    return [await client.next_event(timeout=TIMEOUT) for _ in range(count)]


class TestReplayBuffer:
    def test_revisions_start_at_one_and_increment(self) -> None:
        buffer = _ReplayBuffer()

        assert buffer.add({"type": "a"})["revision"] == 1
        assert buffer.add({"type": "b"})["revision"] == 2
        assert buffer.latest_revision == 2

    def test_the_buffered_copy_carries_its_revision(self) -> None:
        """A replayed event must be numbered, or the client cannot advance."""
        buffer = _ReplayBuffer()
        buffer.add({"type": "a"})

        events, replayed = buffer.since(0)

        assert replayed is True
        assert events[0]["revision"] == 1

    def test_since_returns_only_later_events(self) -> None:
        buffer = _ReplayBuffer()
        for name in "abcd":
            buffer.add({"type": name})

        events, replayed = buffer.since(2)

        assert replayed is True
        assert [e["type"] for e in events] == ["c", "d"]

    def test_being_current_is_a_successful_empty_replay(self) -> None:
        buffer = _ReplayBuffer()
        buffer.add({"type": "a"})

        assert buffer.since(1) == ([], True)

    def test_an_empty_buffer_replays_nothing_successfully(self) -> None:
        assert _ReplayBuffer().since(0) == ([], True)

    def test_eviction_by_count(self) -> None:
        buffer = _ReplayBuffer(max_events=2)
        for name in "abc":
            buffer.add({"type": name})

        assert len(buffer) == 2
        assert buffer.oldest_revision == 2

    def test_eviction_by_bytes(self) -> None:
        """Count alone would let a few enormous events hold megabytes."""
        buffer = _ReplayBuffer(max_events=1000, max_bytes=200)
        for _ in range(10):
            buffer.add({"type": "big", "pad": "x" * 100})

        assert len(buffer) < 10

    def test_a_gap_is_reported_rather_than_papered_over(self) -> None:
        """The whole point: an evicted request must not look like success."""
        buffer = _ReplayBuffer(max_events=2)
        for name in "abcd":
            buffer.add({"type": name})

        events, replayed = buffer.since(1)

        assert replayed is False
        assert events == []

    def test_the_oldest_retained_revision_is_replayable(self) -> None:
        """Off-by-one guard on the boundary between success and gap."""
        buffer = _ReplayBuffer(max_events=2)
        for name in "abc":
            buffer.add({"type": name})

        # Retained are 2 and 3, so asking "everything after 1" is satisfiable.
        events, replayed = buffer.since(1)

        assert replayed is True
        assert [e["type"] for e in events] == ["b", "c"]

    def test_a_revision_ahead_of_the_server_is_refused(self) -> None:
        """A restarted server renumbers from zero; the client must be told."""
        buffer = _ReplayBuffer()
        buffer.add({"type": "a"})

        assert buffer.since(99) == ([], False)

    def test_negative_bounds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            _ReplayBuffer(max_events=-1)


@pytest.mark.asyncio
class TestRevisionsOnTheWire:
    async def test_ready_reports_the_current_revision(self, socket_path):
        async with _serving(socket_path) as server:
            server.broadcast({"type": "before_anyone_connected"})

            async with _client(socket_path) as (_, ready):
                assert ready["revision"] == 1

    async def test_settled_events_are_numbered(self, socket_path):
        async with _serving(socket_path) as server, _client(socket_path) as (client, _):
            server.broadcast({"type": "one"})
            server.broadcast({"type": "two"})

            events = await _drain(client, 2)

            assert [e["revision"] for e in events] == [1, 2]

    async def test_streaming_deltas_are_not_numbered(self, socket_path):
        """They are superseded by message_end, so they are neither kept nor
        numbered — numbering them would create gaps a replay could not fill."""
        async with _serving(socket_path) as server, _client(socket_path) as (client, _):
            server.broadcast({"type": "message_update", "text": "partial"})
            server.broadcast({"type": "message_end"})

            delta, settled = await _drain(client, 2)

            assert "revision" not in delta
            assert settled["revision"] == 1, "the delta must not consume a revision"

    async def test_the_client_tracks_its_position(self, socket_path):
        async with _serving(socket_path) as server, _client(socket_path) as (client, _):
            server.broadcast({"type": "one"})
            server.broadcast({"type": "two"})
            await _drain(client, 2)

            assert client.last_revision == 2


@pytest.mark.asyncio
class TestResume:
    async def test_a_reconnecting_client_receives_what_it_missed(self, socket_path):
        async with _serving(socket_path) as server:
            async with _client(socket_path) as (first, _):
                server.broadcast({"type": "seen"})
                await _drain(first, 1)
                cursor = first.last_revision

            # Disconnected: these are missed.
            server.broadcast({"type": "missed_one"})
            server.broadcast({"type": "missed_two"})

            async with _client(socket_path) as (second, _):
                reply = await second.resume(since=cursor, timeout=TIMEOUT)

                assert reply["replayed"] is True
                assert reply["count"] == 2
                replayed = await _drain(second, 2)
                assert [e["type"] for e in replayed] == ["missed_one", "missed_two"]

    async def test_resume_defaults_to_the_clients_own_cursor(self, socket_path):
        async with _serving(socket_path) as server, _client(socket_path) as (client, _):
            server.broadcast({"type": "one"})
            await _drain(client, 1)

            reply = await client.resume(timeout=TIMEOUT)

            assert reply["replayed"] is True
            assert reply["count"] == 0, "already current"

    async def test_replayed_events_advance_the_cursor(self, socket_path):
        async with _serving(socket_path) as server:
            async with _client(socket_path) as (first, _):
                server.broadcast({"type": "seen"})
                await _drain(first, 1)
                cursor = first.last_revision

            server.broadcast({"type": "missed"})

            async with _client(socket_path) as (second, _):
                await second.resume(since=cursor, timeout=TIMEOUT)
                await _drain(second, 1)

                assert second.last_revision == 2

    async def test_an_evicted_cursor_is_reported_not_faked(self, socket_path):
        """The failure that matters: the client must learn it cannot catch up."""
        async with (
            _serving(socket_path, max_replay_events=2) as server,
            _client(socket_path) as (client, _),
        ):
            for index in range(6):
                server.broadcast({"type": f"event_{index}"})
            await _drain(client, 6)

            reply = await client.resume(since=1, timeout=TIMEOUT)

            assert reply["replayed"] is False
            assert "no longer buffered" in reply["reason"]
            assert reply["count"] == 0

    async def test_a_cursor_ahead_of_the_server_is_reported(self, socket_path):
        async with _serving(socket_path), _client(socket_path) as (client, _):
            reply = await client.resume(since=500, timeout=TIMEOUT)

            assert reply["replayed"] is False
            assert "ahead of this server" in reply["reason"]

    @pytest.mark.parametrize("bad", ["ten", -1, True, None, 1.5])
    async def test_a_malformed_since_is_refused(self, socket_path, bad):
        async with _serving(socket_path), _client(socket_path) as (client, _):
            reply = await client.request({"type": "resume", "since": bad}, timeout=TIMEOUT)

            assert reply["replayed"] is False
            assert "non-negative integer" in reply["reason"]

    async def test_resume_does_not_reach_the_runtime_dispatcher(self, socket_path):
        """It is a transport concern; the dispatcher would call it unknown."""
        async with _serving(socket_path), _client(socket_path) as (client, _):
            reply = await client.resume(since=0, timeout=TIMEOUT)

            assert reply["type"] == "resumed"
            assert "Unknown command" not in str(reply)
