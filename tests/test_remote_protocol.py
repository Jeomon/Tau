"""Tests for tau/remote/protocol.py.

The property worth guarding is that the socket serializes *identically* to
stdio RPC. If these two ever diverge, a client written against one silently
misreads the other, which is the failure mode the shared encoder exists to
prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau.modes import wire
from tau.modes.rpc.types import PROTOCOL_VERSION as RPC_PROTOCOL_VERSION
from tau.remote.framing import FRAME_HEADER_LENGTH, FrameDecoder
from tau.remote.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    decode_message,
    encode_message,
)


def _payload_of(frame: bytes) -> bytes:
    return frame[FRAME_HEADER_LENGTH:]


class TestEncode:
    def test_encodes_into_a_decodable_frame(self) -> None:
        frame = encode_message({"type": "ready"})

        assert list(FrameDecoder().feed(frame)) == [b'{"type": "ready"}']

    def test_round_trip_preserves_the_message(self) -> None:
        message = {"type": "response", "id": 7, "ok": True, "nested": {"a": [1, 2]}}

        frames = list(FrameDecoder().feed(encode_message(message)))

        assert decode_message(frames[0]) == message

    def test_exotic_values_use_the_same_fallback_as_stdio_rpc(self) -> None:
        """Not "it serializes somehow" — it serializes the way wire.py does."""
        message = {"type": "event", "path": Path("/tmp/x"), "blob": b"ab"}

        assert _payload_of(encode_message(message)).decode() == json.dumps(
            message, default=wire.json_default
        )

    def test_payload_matches_the_stdio_line_without_the_newline(self) -> None:
        message = {"type": "ready", "protocolVersion": PROTOCOL_VERSION}

        assert _payload_of(encode_message(message)).decode() == wire.encode_line(message).rstrip(
            "\n"
        )

    def test_non_ascii_survives_the_round_trip(self) -> None:
        message = {"type": "prompt", "text": "héllo ▲ 🎉"}

        assert decode_message(_payload_of(encode_message(message))) == message


class TestDecode:
    def test_rejects_invalid_utf8(self) -> None:
        with pytest.raises(ProtocolError, match="UTF-8"):
            decode_message(b"\xff\xfe")

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises(ProtocolError, match="JSON"):
            decode_message(b"{not json")

    @pytest.mark.parametrize("payload", [b"[1, 2]", b'"text"', b"42", b"null"])
    def test_rejects_json_that_is_not_an_object(self, payload: bytes) -> None:
        """Every message is keyed by ``type``; a non-object cannot carry one."""
        with pytest.raises(ProtocolError, match="JSON object"):
            decode_message(payload)

    def test_accepts_an_empty_object(self) -> None:
        assert decode_message(b"{}") == {}


class TestVersion:
    def test_reexports_the_rpc_protocol_version(self) -> None:
        """One version for both transports, not two that drift."""
        assert PROTOCOL_VERSION is RPC_PROTOCOL_VERSION
