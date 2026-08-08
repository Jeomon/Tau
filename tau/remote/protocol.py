"""Message encoding for the remote protocol.

A frame's payload is a UTF-8 JSON object with exactly the shape stdio RPC
already uses — same keys, same ``snake_case`` event fields, same serialization
fallbacks. That is deliberate: the socket changes how bytes are delimited, not
what they mean, so a client that can talk to ``--mode rpc`` needs no second
vocabulary and the two surfaces cannot drift apart in what they emit.

``tau.modes.wire.json_default`` is reused rather than reimplemented for the
same reason. It is the function that decides how a ``Path``, an enum, or a
stray dataclass reaches a client, and two copies of that decision would diverge
the first time one of them learned a new type.
"""

from __future__ import annotations

import json
from typing import Any

from tau.modes.rpc.types import PROTOCOL_VERSION
from tau.modes.wire import json_default
from tau.remote.framing import encode_frame

__all__ = [
    "PROTOCOL_VERSION",
    "ProtocolError",
    "decode_message",
    "encode_message",
]


class ProtocolError(Exception):
    """A frame's payload was not a decodable protocol message.

    Distinct from ``FrameError``, and the difference decides how a caller
    reacts. A framing error means the byte stream is misaligned and nothing
    after it can be trusted, so the connection dies. A protocol error means one
    message was malformed while the stream stayed in step: the right response
    is an error reply and carrying on, since dropping a session over a client's
    single bad request would be its own bug.
    """


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize one protocol message into a length-prefixed frame."""
    payload = json.dumps(message, default=json_default).encode("utf-8")
    return encode_frame(payload)


def decode_message(payload: bytes) -> dict[str, Any]:
    """Parse one frame payload into a protocol message.

    Raises ``ProtocolError`` for anything that is not a JSON object. Arrays and
    bare scalars are rejected rather than coerced: every message in this
    protocol is keyed by ``type``, so a payload without keys has nowhere to
    carry one and would fail less clearly further in.
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"message is not valid UTF-8: {exc}") from exc
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"message is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError(f"message must be a JSON object, got {type(decoded).__name__}")
    return decoded
