"""Length-prefixed framing for the remote protocol.

Tau's stdio RPC delimits messages with newlines, which works because both ends
are the same process tree and the payload is JSON that never contains a raw
newline. A socket has neither guarantee: a partial write is normal, a peer can
be hostile or merely broken, and "read until newline" on a stream that never
sends one is an unbounded allocation driven by whoever is on the other end.

So a frame is its unsigned 32-bit big-endian byte length followed by that many
bytes. The length is read before any payload memory is committed, which is what
lets ``max_frame_length`` refuse an oversized frame *before* buffering it
rather than after.

Modelled on ``packages/protocol/src/framing.ts`` in the pi agent toolkit, whose
4-byte big-endian header and 16 MiB default this matches so the two remain
wire-compatible if anyone ever points one at the other.
"""

from __future__ import annotations

from collections.abc import Iterator

__all__ = [
    "DEFAULT_MAX_FRAME_LENGTH",
    "FRAME_HEADER_LENGTH",
    "FrameDecoder",
    "FrameError",
    "encode_frame",
]

FRAME_HEADER_LENGTH = 4
_MAX_UINT32 = 0xFFFF_FFFF

#: Upper bound for one frame's payload. Large enough for a transcript export or
#: a base64 image attachment, small enough that a bogus length cannot ask for
#: gigabytes. A peer needing more should stream, not send one enormous frame.
DEFAULT_MAX_FRAME_LENGTH = 16 * 1024 * 1024


class FrameError(Exception):
    """A frame could not be encoded or decoded.

    Always fatal for the connection: framing errors mean the byte stream is no
    longer aligned, so there is no safe way to resynchronise short of dropping
    it. Callers should close rather than skip and continue.
    """


def encode_frame(payload: bytes) -> bytes:
    """Prefix ``payload`` with its length as a 4-byte big-endian header."""
    if len(payload) > _MAX_UINT32:
        raise FrameError(f"payload of {len(payload)} bytes exceeds the 32-bit length header")
    return len(payload).to_bytes(FRAME_HEADER_LENGTH, "big") + payload


class FrameDecoder:
    """Incremental decoder turning a byte stream into whole frames.

    Feed it whatever arrives — a socket read may split a frame across chunks or
    deliver several at once — and it yields only complete payloads. Partial
    data is retained until the rest turns up.
    """

    def __init__(self, *, max_frame_length: int = DEFAULT_MAX_FRAME_LENGTH) -> None:
        if not 0 <= max_frame_length <= _MAX_UINT32:
            raise ValueError(f"max_frame_length must be within 0..{_MAX_UINT32}")
        self._max_frame_length = max_frame_length
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        """Bytes buffered but not yet forming a complete frame."""
        return len(self._buffer)

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Add received bytes and yield every frame they complete.

        Yields lazily, so a caller that stops consuming mid-iteration leaves
        the remaining frames buffered rather than losing them.
        """
        self._buffer.extend(chunk)
        while True:
            if len(self._buffer) < FRAME_HEADER_LENGTH:
                return
            length = int.from_bytes(self._buffer[:FRAME_HEADER_LENGTH], "big")
            if length > self._max_frame_length:
                # Raised on the header, before the payload is buffered — the
                # whole reason the length goes first.
                raise FrameError(
                    f"frame of {length} bytes exceeds the {self._max_frame_length}-byte limit"
                )
            end = FRAME_HEADER_LENGTH + length
            if len(self._buffer) < end:
                return
            payload = bytes(self._buffer[FRAME_HEADER_LENGTH:end])
            del self._buffer[:end]
            yield payload
