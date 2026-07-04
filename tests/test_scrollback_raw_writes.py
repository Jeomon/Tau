"""Tests for ScrollbackTerminal's raw_writes flush mechanism (Stage 5 / Image).

Covers the gap a plain cell-diff can't handle: a row that's entirely
skip=True cells (e.g. a freshly rendered inline image) never registers as
"changed" to _row_equal (no non-skip cell ever differs), so it needs its
own independent novelty-tracked flush — see ScrollbackTerminal._flush_raw_writes.
"""

from __future__ import annotations

from tau.tui.buffer import Buffer, RawWrite
from tau.tui.frame import ScrollbackTerminal
from tau.tui.geometry import Rect


class FakeTerminal:
    def __init__(self, width: int = 40, height: int = 10) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def on_resize(self, callback: object) -> object:
        return lambda: None


def _image_buf(width: int, rows: int, y: int, token: str, data: str = "\x1b_Gfake\x1b\\") -> Buffer:
    buf = Buffer.empty(Rect(0, 0, width, 0))
    buf.grow_to(y + rows)
    for yy in range(y, y + rows):
        for xx in range(width):
            buf.set(xx, yy, " ")
            buf.get(xx, yy).skip = True
    buf.raw_writes.append(RawWrite(0, y, data, token))
    return buf


def test_all_skip_row_still_sends_raw_write_on_first_render() -> None:
    term = FakeTerminal()
    engine = ScrollbackTerminal(term)  # type: ignore[arg-type]

    engine.render(_image_buf(40, 3, 0, token="img-1"))

    assert any("\x1b_Gfake" in w for w in term.writes)


def test_isolated_new_image_row_still_sends_when_no_other_row_changed() -> None:
    """The gap this mechanism exists for: appending a brand-new all-skip row
    with nothing else in the buffer changing must not be silently dropped by
    the "no changes" early-return path in the main cell-diff loop."""
    term = FakeTerminal()
    engine = ScrollbackTerminal(term)  # type: ignore[arg-type]

    engine.render(_image_buf(40, 2, 0, token="img-1"))
    term.writes.clear()

    # Buffer grows by appending a second, isolated image two rows down —
    # first image's rows are identical (still all-skip => "unchanged").
    buf2 = _image_buf(40, 2, 0, token="img-1")
    buf2.grow_to(5)
    for yy in range(3, 5):
        for xx in range(40):
            buf2.set(xx, yy, " ")
            buf2.get(xx, yy).skip = True
    buf2.raw_writes.append(RawWrite(0, 3, "\x1b_Gsecond\x1b\\", "img-2"))

    engine.render(buf2)

    assert any("\x1b_Gsecond" in w for w in term.writes)
    # First image's token hasn't changed, so it must not be resent.
    assert not any("\x1b_Gfake" in w for w in term.writes)


def test_unchanged_token_is_not_resent() -> None:
    term = FakeTerminal()
    engine = ScrollbackTerminal(term)  # type: ignore[arg-type]

    engine.render(_image_buf(40, 2, 0, token="img-1"))
    term.writes.clear()

    engine.render(_image_buf(40, 2, 0, token="img-1"))

    assert not any("\x1b_Gfake" in w for w in term.writes)


def test_changed_token_at_same_position_resends() -> None:
    term = FakeTerminal()
    engine = ScrollbackTerminal(term)  # type: ignore[arg-type]

    engine.render(_image_buf(40, 2, 0, token="img-1", data="\x1b_Gone\x1b\\"))
    term.writes.clear()

    engine.render(_image_buf(40, 2, 0, token="img-2", data="\x1b_Gtwo\x1b\\"))

    assert any("\x1b_Gtwo" in w for w in term.writes)


def test_clear_forces_resend() -> None:
    term = FakeTerminal()
    engine = ScrollbackTerminal(term)  # type: ignore[arg-type]

    engine.render(_image_buf(40, 2, 0, token="img-1"))
    engine.clear()
    term.writes.clear()

    engine.render(_image_buf(40, 2, 0, token="img-1"))

    assert any("\x1b_Gfake" in w for w in term.writes)
