"""Tests for run_remote_mode and the stale-socket sweep.

``run_remote_mode`` is what ``--mode remote`` actually executes, and it was the
one part of the remote stack with no automated coverage — verified by hand
against a live process, which proves it worked once but protects nothing.
Everything it wires up (the socket, the UI bridge, the shutdown handler, the
teardown) is asserted here instead.

The sweep exists because socket paths are named for their session and so are
never reused: the replace-on-bind check in ``RemoteServer.start`` never
revisits a path, so a server killed without unwinding leaves a file that
nothing else would ever remove.
"""

from __future__ import annotations

import asyncio
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.remote.mode import run_remote_mode
from tau.remote.client import RemoteClient
from tau.remote.server import sweep_stale_sockets

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


class _FakeRuntime:
    """Records the wiring run_remote_mode is supposed to install."""

    def __init__(self, session_id: str = "test-session") -> None:
        self.hooks = _FakeHooks()
        self.agent = None
        self.session_manager = SimpleNamespace(session_id=session_id)
        self.shutdown_handler = None
        self.ui_bridge = None

    def set_shutdown_handler(self, handler) -> None:
        self.shutdown_handler = handler

    def set_extension_ui_bridge(self, bridge) -> None:
        self.ui_bridge = bridge


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """Short enough for sun_path; pytest's tmp_path is not."""
    with tempfile.TemporaryDirectory(prefix="tau-run-") as directory:
        yield Path(directory)


def _make_stale_socket(path: Path) -> Path:
    """A socket file with nobody behind it, as a crash leaves one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.close()  # closed without unlinking
    return path


async def _serve(runtime: _FakeRuntime, path: Path):
    """Start run_remote_mode and wait until it is actually listening."""
    task = asyncio.ensure_future(run_remote_mode(runtime, str(path)))
    for _ in range(100):
        if path.exists() and runtime.shutdown_handler is not None:
            return task
        await asyncio.sleep(0.02)
    task.cancel()
    raise AssertionError("run_remote_mode never started listening")


async def _stop(runtime: _FakeRuntime, task) -> None:
    assert runtime.shutdown_handler is not None
    runtime.shutdown_handler()
    await asyncio.wait_for(task, TIMEOUT)


class TestRunRemoteMode:
    async def test_it_serves_a_connectable_socket(self, socket_dir):
        runtime = _FakeRuntime()
        path = socket_dir / "s.sock"
        task = await _serve(runtime, path)
        try:
            client = RemoteClient(path)
            ready = await asyncio.wait_for(client.connect(), TIMEOUT)

            assert ready["type"] == "ready"
            await client.close()
        finally:
            await _stop(runtime, task)

    async def test_it_installs_the_shutdown_handler(self, socket_dir):
        """ctx.shutdown() has to unwind through here, not sys.exit()."""
        runtime = _FakeRuntime()
        task = await _serve(runtime, socket_dir / "s.sock")

        assert callable(runtime.shutdown_handler)
        await _stop(runtime, task)

    async def test_it_points_extension_dialogs_at_the_clients(self, socket_dir):
        runtime = _FakeRuntime()
        assert runtime.ui_bridge is None, "no bridge before serving: no client can be reached yet"
        path = socket_dir / "s.sock"
        task = await _serve(runtime, path)
        try:
            client = RemoteClient(path)
            await asyncio.wait_for(client.connect(), TIMEOUT)

            assert runtime.ui_bridge is not None
            runtime.ui_bridge._fire({"method": "notify", "message": "hello"})

            event = await client.next_event(timeout=TIMEOUT)
            assert event["type"] == "extension_ui_request"
            assert event["method"] == "notify"
            await client.close()
        finally:
            await _stop(runtime, task)

    async def test_shutdown_removes_the_socket(self, socket_dir):
        runtime = _FakeRuntime()
        path = socket_dir / "s.sock"
        task = await _serve(runtime, path)
        assert path.exists()

        await _stop(runtime, task)

        assert not path.exists()

    async def test_it_does_not_leave_a_signal_handler_behind(self, socket_dir):
        """A handler left on the shared loop would point at a dead runtime."""
        import signal

        runtime = _FakeRuntime()
        task = await _serve(runtime, socket_dir / "s.sock")
        await _stop(runtime, task)

        loop = asyncio.get_running_loop()
        # remove_signal_handler returns False when nothing was installed.
        assert loop.remove_signal_handler(signal.SIGINT) is False

    async def test_it_sweeps_stale_siblings_on_start(self, socket_dir):
        stale = _make_stale_socket(socket_dir / "old.sock")
        runtime = _FakeRuntime()
        task = await _serve(runtime, socket_dir / "s.sock")
        try:
            assert not stale.exists(), "a dead sibling socket should be swept"
        finally:
            await _stop(runtime, task)


class TestSweepStaleSockets:
    async def test_it_removes_a_dead_socket(self, socket_dir):
        stale = _make_stale_socket(socket_dir / "dead.sock")

        removed = sweep_stale_sockets(socket_dir)

        assert removed == [stale]
        assert not stale.exists()

    async def test_it_leaves_a_live_socket_alone(self, socket_dir):
        live = socket_dir / "live.sock"
        server = await asyncio.start_unix_server(lambda r, w: None, path=str(live))
        try:
            removed = sweep_stale_sockets(socket_dir)

            assert removed == []
            assert live.exists()
        finally:
            server.close()
            await server.wait_closed()

    async def test_it_never_touches_a_regular_file(self, socket_dir):
        """Only sockets are candidates; a user's file is not ours to delete."""
        regular = socket_dir / "notes.sock"
        regular.write_text("precious")

        removed = sweep_stale_sockets(socket_dir)

        assert removed == []
        assert regular.read_text() == "precious"

    async def test_keep_exempts_the_path_being_bound(self, socket_dir):
        """That one belongs to start(), which reports a live holder as an error."""
        mine = _make_stale_socket(socket_dir / "mine.sock")
        other = _make_stale_socket(socket_dir / "other.sock")

        removed = sweep_stale_sockets(socket_dir, keep=mine)

        assert removed == [other]
        assert mine.exists()

    async def test_a_missing_directory_is_not_an_error(self, socket_dir):
        """First run: nothing has been created yet."""
        assert sweep_stale_sockets(socket_dir / "nope") == []

    async def test_non_socket_suffixes_are_ignored(self, socket_dir):
        other = socket_dir / "session.log"
        other.write_text("log")

        assert sweep_stale_sockets(socket_dir) == []
        assert other.exists()
