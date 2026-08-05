"""String-level overlay compositing must match the cell renderer's blit.

Renderer._composite_overlays blits an overlay Buffer's cells into the frame
Buffer. compose.composite_lines does the same thing for the string renderer.
These tests drive both with the same inputs and require identical results —
including the cases where column arithmetic is easy to get wrong: wide glyphs,
ANSI runs spanning the splice, and overlays hanging off either edge.
"""

from __future__ import annotations

import pytest

from tau.tui.ansi_bridge import parse_ansi_into, row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.compose import composite_line, composite_lines
from tau.tui.geometry import Rect

WIDTH = 40


def _blit_via_cells(
    base_lines: list[str],
    overlay_lines: list[str],
    row: int,
    col: int,
    overlay_width: int,
    total_width: int,
) -> list[str]:
    """What Renderer._composite_overlays does, reduced to lines in and out."""
    height = max(len(base_lines), row + len(overlay_lines))
    buf = Buffer.empty(Rect(0, 0, total_width, height))
    for y, line in enumerate(base_lines):
        parse_ansi_into(buf, 0, y, line, total_width)

    ov = Buffer.empty(Rect(0, 0, overlay_width, len(overlay_lines)))
    for y, line in enumerate(overlay_lines):
        parse_ansi_into(ov, 0, y, line, overlay_width)

    for y in range(len(overlay_lines)):
        target_y = row + y
        if target_y < 0:
            continue
        src_base = y * overlay_width
        dst_base = target_y * total_width
        for x in range(overlay_width):
            target_x = col + x
            if target_x < 0 or target_x >= total_width:
                continue
            buf.content[dst_base + target_x] = ov.content[src_base + x]

    return [row_to_ansi(buf, y, embed_raw=True, trim_trailing_blanks=True) for y in range(height)]


def _same_pixels(a: list[str], b: list[str], width: int = WIDTH) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        pa = Buffer.empty(Rect(0, 0, width, 1))
        pb = Buffer.empty(Rect(0, 0, width, 1))
        parse_ansi_into(pa, 0, 0, ra, width)
        parse_ansi_into(pb, 0, 0, rb, width)
        if pa.content != pb.content:
            return False
    return True


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
def test_matches_the_cell_blit(
    name: str, overlay: list[str], row: int, col: int, ov_w: int
) -> None:
    expected = _blit_via_cells(BASE, overlay, row, col, ov_w, WIDTH)
    got = composite_lines(BASE, overlay, row, col, ov_w, WIDTH)
    assert _same_pixels(expected, got)


def test_wide_glyphs_in_the_base_are_not_split_wrongly() -> None:
    base = ["日本語のテキストです here"]
    overlay = ["[OK]"]
    expected = _blit_via_cells(base, overlay, 0, 6, 4, WIDTH)
    got = composite_lines(base, overlay, 0, 6, 4, WIDTH)
    assert _same_pixels(expected, got)


def test_wide_glyphs_in_the_overlay() -> None:
    base = ["plain ascii base line that is long enough"]
    overlay = ["日本"]
    expected = _blit_via_cells(base, overlay, 0, 5, 4, WIDTH)
    got = composite_lines(base, overlay, 0, 5, 4, WIDTH)
    assert _same_pixels(expected, got)


def test_ansi_run_spanning_the_splice_point() -> None:
    base = ["\x1b[1;34mbold blue running across the whole line\x1b[0m"]
    overlay = ["\x1b[41mRED\x1b[0m"]
    expected = _blit_via_cells(base, overlay, 0, 10, 3, WIDTH)
    got = composite_lines(base, overlay, 0, 10, 3, WIDTH)
    assert _same_pixels(expected, got)


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
    assert _same_pixels(got, _blit_via_cells(BASE, ["ZZZZ"], 0, WIDTH + 5, 4, WIDTH))


def test_base_is_extended_when_overlay_reaches_past_it() -> None:
    got = composite_lines(["only one"], ["a", "b", "c"], 2, 0, 1, WIDTH)
    assert len(got) == 5
