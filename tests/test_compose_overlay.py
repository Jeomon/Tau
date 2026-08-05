"""String-level overlay compositing.

``compose.composite_lines`` paints an overlay's lines over the frame's lines.
The hard part is column arithmetic: wide glyphs occupying two columns, ANSI
runs spanning the splice point, and overlays hanging off either edge. These
assert the observable result directly.
"""

from __future__ import annotations

import pytest

from tau.tui.compose import composite_line, composite_lines
from tau.tui.utils import strip_ansi, visible_width

WIDTH = 40

BASE = [
    "the quick brown fox jumps over it",
    "\x1b[31mred line of base content here\x1b[0m",
    "third line of base content",
    "fourth line here",
]

CASES = [
    ("centred popup", ["+----+", "|hi  |", "+----+"], 1, 10, 6),
    ("at origin", ["XX", "YY"], 0, 0, 2),
    ("hanging off right", ["ABCDEFGH"], 1, WIDTH - 3, 8),
    ("hanging off left", ["ABCDEFGH"], 1, -3, 8),
    ("past the last row", ["new", "rows"], 3, 5, 4),
    ("full width", ["=" * WIDTH], 2, 0, WIDTH),
    ("styled overlay", ["\x1b[42mgreen\x1b[0m"], 1, 8, 5),
    ("single cell", ["Z"], 2, 20, 1),
]


@pytest.mark.parametrize(
    ("name", "overlay", "row", "col", "ov_w"), CASES, ids=[c[0] for c in CASES]
)
def test_result_never_exceeds_the_frame_width(
    name: str, overlay: list[str], row: int, col: int, ov_w: int
) -> None:
    for line in composite_lines(BASE, overlay, row, col, ov_w, WIDTH):
        assert visible_width(line) <= WIDTH


@pytest.mark.parametrize(
    ("name", "overlay", "row", "col", "ov_w"), CASES, ids=[c[0] for c in CASES]
)
def test_rows_outside_the_overlay_are_untouched(
    name: str, overlay: list[str], row: int, col: int, ov_w: int
) -> None:
    got = composite_lines(BASE, overlay, row, col, ov_w, WIDTH)
    for i, line in enumerate(BASE):
        if not (row <= i < row + len(overlay)):
            assert got[i] == line


def test_overlay_lands_at_the_requested_column() -> None:
    got = composite_lines(["." * WIDTH], ["ABC"], 0, 10, 3, WIDTH)
    assert strip_ansi(got[0])[10:13] == "ABC"
    assert strip_ansi(got[0])[:10] == "." * 10


def test_overlay_hanging_off_the_left_is_clipped_not_wrapped() -> None:
    got = composite_lines(["." * WIDTH], ["ABCDEFGH"], 0, -3, 8, WIDTH)
    plain = strip_ansi(got[0])
    assert plain.startswith("DEFGH")
    assert visible_width(got[0]) <= WIDTH


def test_overlay_hanging_off_the_right_is_clipped() -> None:
    got = composite_lines(["." * WIDTH], ["ABCDEFGH"], 0, WIDTH - 3, 8, WIDTH)
    plain = strip_ansi(got[0])
    assert plain.endswith("ABC")
    assert visible_width(got[0]) <= WIDTH


def test_a_wide_glyph_in_the_base_survives_a_splice_over_its_continuation() -> None:
    """The glyph still prints; the overlay's first column is swallowed."""
    base = ["日本語のテキストです here"]
    got = composite_lines(base, ["[OK]"], 0, 5, 4, WIDTH)
    assert visible_width(got[0]) <= WIDTH
    assert "\ufffd" not in strip_ansi(got[0])


def test_wide_glyphs_in_the_overlay_are_placed_whole() -> None:
    base = ["plain ascii base line that is long enough"]
    got = composite_lines(base, ["日本"], 0, 5, 4, WIDTH)
    assert "日本" in strip_ansi(got[0])
    assert visible_width(got[0]) <= WIDTH


def test_ansi_run_spanning_the_splice_point_is_reasserted_after_it() -> None:
    base = ["\x1b[1;34mbold blue running across the whole line\x1b[0m"]
    got = composite_lines(base, ["\x1b[41mRED\x1b[0m"], 0, 10, 3, WIDTH)
    assert "RED" in strip_ansi(got[0])
    # The base's styling resumes after the overlay rather than leaking through it.
    assert "\x1b[" in got[0]


def test_overlay_does_not_mutate_the_base_lines() -> None:
    """Frozen rows are shared; compositing must never write back into them."""
    base = list(BASE)
    snapshot = list(BASE)
    composite_lines(base, ["XX"], 1, 3, 2, WIDTH)
    assert base == snapshot


def test_composite_line_is_a_noop_for_zero_width() -> None:
    assert composite_line("abc", "X", 0, 0, WIDTH) == "abc"
    assert composite_line("abc", "X", 0, 1, 0) == "abc"


def test_overlay_entirely_off_screen_leaves_base_unchanged() -> None:
    got = composite_lines(BASE, ["ZZZZ"], 0, WIDTH + 5, 4, WIDTH)
    assert [strip_ansi(x).rstrip() for x in got] == [strip_ansi(x).rstrip() for x in BASE]


def test_base_is_extended_when_overlay_reaches_past_it() -> None:
    got = composite_lines(["only one"], ["a", "b", "c"], 2, 0, 1, WIDTH)
    assert len(got) == 5
