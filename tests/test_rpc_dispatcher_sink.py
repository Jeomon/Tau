"""The RPC dispatcher's response sink is injectable.

``_handle_command`` used to write every response through the module-global
``_write``, which is bound to stdout. That is correct for stdio RPC, where the
process serves exactly one client, and wrong for ``tau.remote``, where several
clients share one dispatcher and a response belongs to whoever asked for it.

The property under test is not merely "a custom sink receives the response" but
also that it *replaces* the global one. A sink that additionally leaked to
stdout would pass a naive test and still broadcast every client's answers to
every other client.
"""

from __future__ import annotations

import pytest

from tau.modes.rpc import mode


class _FakeRuntime:
    """Enough runtime for commands that fail before touching one."""

    def __init__(self) -> None:
        self.agent = None


@pytest.fixture
def global_sink(monkeypatch):
    """Captures anything reaching the module-global stdout writer."""
    written: list[dict] = []
    monkeypatch.setattr(mode, "_write", lambda obj: written.append(obj))
    return written


@pytest.mark.asyncio
async def test_default_sink_is_still_the_global_writer(global_sink):
    """Every existing caller passes no sink and must be unaffected."""
    await mode._handle_command({"type": "nope", "id": "1"}, _FakeRuntime(), {})

    assert len(global_sink) == 1
    assert global_sink[0]["success"] is False
    assert global_sink[0]["command"] == "nope"


@pytest.mark.asyncio
async def test_injected_sink_receives_the_response(global_sink):
    connection: list[dict] = []

    await mode._handle_command(
        {"type": "nope", "id": "7"}, _FakeRuntime(), {}, write=connection.append
    )

    assert len(connection) == 1
    assert connection[0]["id"] == "7"
    assert connection[0]["success"] is False


@pytest.mark.asyncio
async def test_injected_sink_replaces_rather_than_duplicates(global_sink):
    """The multi-client property: nothing reaches stdout behind the sink."""
    connection: list[dict] = []

    await mode._handle_command(
        {"type": "nope", "id": "7"}, _FakeRuntime(), {}, write=connection.append
    )

    assert connection != []
    assert global_sink == []


@pytest.mark.asyncio
async def test_two_connections_get_only_their_own_responses(global_sink):
    """What the seam exists for: one dispatcher, two clients, no crosstalk."""
    first: list[dict] = []
    second: list[dict] = []
    runtime = _FakeRuntime()

    await mode._handle_command({"type": "nope", "id": "a"}, runtime, {}, write=first.append)
    await mode._handle_command({"type": "nope", "id": "b"}, runtime, {}, write=second.append)

    assert [r["id"] for r in first] == ["a"]
    assert [r["id"] for r in second] == ["b"]
    assert global_sink == []


@pytest.mark.asyncio
async def test_explicit_none_falls_back_to_the_global_writer(global_sink):
    """``write=None`` is the documented default, not an accidental no-op."""
    await mode._handle_command({"type": "nope", "id": "1"}, _FakeRuntime(), {}, write=None)

    assert len(global_sink) == 1
