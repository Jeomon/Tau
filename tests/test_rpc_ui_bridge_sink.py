"""The extension UI bridge's request sink is injectable.

The bridge emits `extension_ui_request` to stdout, which is right for stdio RPC
and useless anywhere else: a dialog sent to a stream nobody reads blocks the
extension on an answer that can never arrive. `write` names the destination so
a socket server can point dialogs at its attached clients instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tau.modes.rpc import mode as rpc


class TestExtensionUIBridge:
    """Extension dialogs must reach the socket clients, not stdout.

    Without this an extension calling ctx.select() in remote mode blocks on a
    reply nobody could send, because the request went to a stream no client is
    reading.
    """

    @pytest.fixture(autouse=True)
    def _reset_bridge(self, monkeypatch):
        monkeypatch.setattr(rpc, "_UI_BRIDGE", None)
        rpc._UI_PENDING.clear()
        yield
        rpc._UI_PENDING.clear()

    def test_dialog_requests_go_to_the_injected_sink(self, monkeypatch) -> None:
        stdout_writes: list[dict] = []
        monkeypatch.setattr(rpc, "_write", lambda obj: stdout_writes.append(obj))
        sink: list[dict] = []
        runtime = SimpleNamespace(set_extension_ui_bridge=lambda bridge: None)

        bridge = rpc.install_extension_ui_bridge(runtime, write=sink.append)
        bridge._fire({"method": "notify", "message": "hi"})

        assert len(sink) == 1
        assert sink[0]["type"] == "extension_ui_request"
        assert stdout_writes == [], "must not also reach stdout"

    def test_an_existing_stdout_bridge_is_redirected(self, monkeypatch) -> None:
        """Runtime.create may install a bridge before the server exists."""
        stdout_writes: list[dict] = []
        monkeypatch.setattr(rpc, "_write", lambda obj: stdout_writes.append(obj))
        runtime = SimpleNamespace(set_extension_ui_bridge=lambda bridge: None)

        first = rpc.install_extension_ui_bridge(runtime)  # stdout-bound
        sink: list[dict] = []
        second = rpc.install_extension_ui_bridge(runtime, write=sink.append)

        assert first is second, "the same instance keeps pending dialogs alive"
        second._fire({"method": "notify", "message": "hi"})
        assert len(sink) == 1
        assert stdout_writes == []

    def test_the_default_bridge_still_writes_to_stdout(self, monkeypatch) -> None:
        """RPC mode must be untouched by the new parameter."""
        stdout_writes: list[dict] = []
        monkeypatch.setattr(rpc, "_write", lambda obj: stdout_writes.append(obj))
        runtime = SimpleNamespace(set_extension_ui_bridge=lambda bridge: None)

        bridge = rpc.install_extension_ui_bridge(runtime)
        bridge._fire({"method": "notify", "message": "hi"})

        assert len(stdout_writes) == 1
