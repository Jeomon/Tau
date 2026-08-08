"""Tests for tau/remote/framing.py.

The reason this exists rather than reusing newline delimiting: a socket peer
can be slow, hostile, or merely wrong, and "read until newline" on a stream
that never sends one allocates without bound at the peer's discretion. The
tests that matter most here are the ones about *partial* and *oversized* input,
since those are the cases a pipe between related processes never produces and a
socket produces routinely.
"""

from __future__ import annotations

import pytest

from tau.remote.framing import (
    DEFAULT_MAX_FRAME_LENGTH,
    FRAME_HEADER_LENGTH,
    FrameDecoder,
    FrameError,
    encode_frame,
)


def _decode_all(decoder: FrameDecoder, chunk: bytes) -> list[bytes]:
    return list(decoder.feed(chunk))


class TestEncode:
    def test_header_is_four_byte_big_endian_length(self) -> None:
        frame = encode_frame(b"hello")

        assert frame[:FRAME_HEADER_LENGTH] == (5).to_bytes(4, "big")
        assert frame[FRAME_HEADER_LENGTH:] == b"hello"

    def test_empty_payload_is_a_valid_frame(self) -> None:
        """A zero-length frame is legal and must not be read as end-of-stream."""
        assert encode_frame(b"") == b"\x00\x00\x00\x00"


class TestDecode:
    def test_round_trip(self) -> None:
        assert _decode_all(FrameDecoder(), encode_frame(b"payload")) == [b"payload"]

    def test_several_frames_in_one_chunk(self) -> None:
        chunk = encode_frame(b"one") + encode_frame(b"two") + encode_frame(b"three")

        assert _decode_all(FrameDecoder(), chunk) == [b"one", b"two", b"three"]

    def test_payload_split_across_chunks(self) -> None:
        frame = encode_frame(b"split me")
        decoder = FrameDecoder()

        assert _decode_all(decoder, frame[:6]) == []
        assert _decode_all(decoder, frame[6:]) == [b"split me"]

    def test_header_split_across_chunks(self) -> None:
        """The nastier split: fewer bytes than the header itself."""
        frame = encode_frame(b"x")
        decoder = FrameDecoder()

        assert _decode_all(decoder, frame[:2]) == []
        assert _decode_all(decoder, frame[2:3]) == []
        assert _decode_all(decoder, frame[3:]) == [b"x"]

    def test_byte_at_a_time_delivery(self) -> None:
        frame = encode_frame(b"drip")
        decoder = FrameDecoder()
        out: list[bytes] = []
        for index in range(len(frame)):
            out.extend(decoder.feed(frame[index : index + 1]))

        assert out == [b"drip"]

    def test_empty_frame_decodes_to_empty_payload(self) -> None:
        assert _decode_all(FrameDecoder(), encode_frame(b"")) == [b""]

    def test_trailing_partial_frame_is_retained(self) -> None:
        decoder = FrameDecoder()
        chunk = encode_frame(b"complete") + encode_frame(b"partial")[:5]

        assert _decode_all(decoder, chunk) == [b"complete"]
        assert decoder.pending_bytes == 5

    def test_stopping_mid_iteration_does_not_lose_frames(self) -> None:
        """feed() is lazy; a caller that breaks out must not drop the rest."""
        decoder = FrameDecoder()
        chunk = encode_frame(b"first") + encode_frame(b"second")

        frames = decoder.feed(chunk)
        assert next(frames) == b"first"
        del frames  # abandon the generator

        assert _decode_all(decoder, b"") == [b"second"]


class TestLimits:
    def test_oversized_frame_is_refused(self) -> None:
        decoder = FrameDecoder(max_frame_length=8)

        with pytest.raises(FrameError, match="exceeds"):
            _decode_all(decoder, encode_frame(b"far too long for eight"))

    def test_the_limit_is_enforced_from_the_header_alone(self) -> None:
        """The point of putting the length first: an absurd claim costs four
        bytes, not the allocation it asks for. Only the header is fed here — no
        payload exists, and none is waited for."""
        decoder = FrameDecoder(max_frame_length=1024)
        header = (4 * 1024 * 1024 * 1024 - 1).to_bytes(4, "big")

        with pytest.raises(FrameError, match="exceeds"):
            _decode_all(decoder, header)

    def test_a_frame_exactly_at_the_limit_is_accepted(self) -> None:
        decoder = FrameDecoder(max_frame_length=4)

        assert _decode_all(decoder, encode_frame(b"abcd")) == [b"abcd"]

    def test_default_limit_is_sixteen_mebibytes(self) -> None:
        assert DEFAULT_MAX_FRAME_LENGTH == 16 * 1024 * 1024

    @pytest.mark.parametrize("invalid", [-1, 0x1_0000_0000])
    def test_limit_outside_the_header_range_is_rejected(self, invalid: int) -> None:
        with pytest.raises(ValueError, match="max_frame_length"):
            FrameDecoder(max_frame_length=invalid)

    def test_zero_limit_admits_only_empty_frames(self) -> None:
        decoder = FrameDecoder(max_frame_length=0)

        assert _decode_all(decoder, encode_frame(b"")) == [b""]
        with pytest.raises(FrameError):
            _decode_all(decoder, encode_frame(b"x"))
