"""iTerm2 inline-image protocol encoding (OSC 1337).

``size`` is optional to iTerm2 itself — it only drives the progress indicator —
but the xterm.js image addon requires it and rejects a sequence without one, so
images never appeared in web terminals or anything else embedding xterm.js.

Everything here goes through ``_encode_iterm2``'s output rather than the size
helper it calls: the emitted sequence is the contract, and a test that reached
for the helper could not run at all against a build that predates it.
"""

from __future__ import annotations

import base64
import os

from tau.tui.components.image import _encode_iterm2

# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _args(sequence: str) -> dict[str, str]:
    """Parse the key=value header of an OSC 1337 File sequence."""
    header = sequence.split(":", 1)[0]
    return dict(part.split("=", 1) for part in header.split("File=", 1)[1].split(";"))


def _declared_size(raw: bytes) -> str | None:
    sequence = _encode_iterm2(base64.b64encode(raw).decode(), cols=10, filename=None)
    return _args(sequence).get("size")


class TestSizeArgument:
    def test_size_is_emitted(self):
        assert _declared_size(PNG) is not None

    def test_size_is_the_decoded_byte_count(self):
        assert _declared_size(PNG) == str(len(PNG))

    def test_size_is_correct_for_every_base64_padding_case(self):
        """0, 1 and 2 padding bytes each need different arithmetic."""
        for n in range(0, 200):
            assert _declared_size(os.urandom(n)) == str(n), n

    def test_size_is_correct_for_a_large_payload(self):
        assert _declared_size(os.urandom(1_000_000)) == "1000000"

    def test_size_describes_the_payload_actually_sent(self):
        """A non-PNG image may be re-encoded before reaching the encoder."""
        assert _declared_size(os.urandom(1234)) == "1234"


class TestOtherArgumentsUnchanged:
    def test_layout_arguments(self):
        args = _args(_encode_iterm2(base64.b64encode(PNG).decode(), cols=7, filename=None))

        assert args["inline"] == "1"
        assert args["width"] == "7"
        assert args["height"] == "auto"
        assert args["preserveAspectRatio"] == "1"

    def test_the_filename_is_still_base64_encoded(self):
        args = _args(_encode_iterm2(base64.b64encode(PNG).decode(), cols=7, filename="shot.png"))

        assert base64.b64decode(args["name"]).decode() == "shot.png"

    def test_the_sequence_is_well_formed(self):
        b64 = base64.b64encode(PNG).decode()

        sequence = _encode_iterm2(b64, cols=10, filename=None)

        assert sequence.startswith("\x1b]1337;File=")
        assert sequence.endswith("\x07")
        assert sequence.split(":", 1)[1][:-1] == b64
