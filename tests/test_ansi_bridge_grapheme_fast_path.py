"""Differential check on parse_ansi_wrapped_into's printable-ASCII fast path.

The fast path skips grapheme segmentation for a printable-ASCII character
followed by another printable-ASCII character (or end of line), on the reasoning
that nothing which *combines* is ASCII. This module pins that reasoning by
comparing against full segmentation rather than trusting it — the dangerous
cases are ASCII characters that do form part of a longer cluster, e.g. the digit
in a keycap ("1\ufe0f\u20e3") or a base letter carrying a combining accent.

Note on continuation cells: a double-width cluster occupies its own cell plus a
follower written as ``""``, which ``Buffer.set`` normalises to ``" "`` — so the
reference layout below uses a space, matching what the buffer actually holds.
"""

from __future__ import annotations

import grapheme
import pytest

from tau.tui.ansi_bridge import parse_ansi_wrapped_into
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.utils import grapheme_width

# Each entry exercises a different branch of the guard.
CORPUS = [
    "hello world",  # pure ASCII — the fast path itself
    "a",  # single char, end-of-line branch
    "",
    " leading and trailing ",
    "punctuation!@#$%^&*()_+-=[]{}|;':\",./<>?",
    "~",  # last printable ASCII codepoint
    " ",  # first printable ASCII codepoint
    "1\ufe0f\u20e3",  # keycap: ASCII digit that IS part of a longer cluster
    "a\u0301",  # base ASCII letter + combining acute
    "e\u0301x\u0300y",  # combining marks interleaved with ASCII
    "\U0001f1fa\U0001f1f8",  # flag: regional indicator pair
    "\U0001f469\u200d\U0001f4bb",  # ZWJ sequence (woman technologist)
    "日本語",  # CJK, all double-width
    "hi 日本 ok",  # ASCII adjacent to double-width
    "café résumé",  # non-ASCII precomposed
    "tab\tseparated",  # ASCII control char (not printable) → slow path
    "\r\n",  # a single cluster under UAX #29
    "a\r\nb",
    "emoji \U0001f600 mixed \U0001f1e6\U0001f1e7 end",
]


def _expected_cells(text: str) -> list[str]:
    """Lay out ``text`` using full grapheme segmentation — the reference."""
    cells: list[str] = []
    for raw in grapheme.graphemes(text):
        cluster = raw or ""
        width = grapheme_width(cluster)
        if width == 0:
            continue
        cells.append(cluster)
        if width == 2:
            cells.append(" ")  # continuation cell for a double-width cluster
    return cells


def _render(text: str, width: int) -> list[list[str]]:
    """Render and return the cell symbols, row by row."""
    buf = Buffer.empty(Rect(0, 0, width, 0))
    rows = parse_ansi_wrapped_into(buf, 0, 0, text, width)
    return [[buf.get(col, row).symbol for col in range(width)] for row in range(rows)]


@pytest.mark.parametrize("text", CORPUS)
def test_fast_path_matches_full_grapheme_segmentation(text: str) -> None:
    """Clusters placed must be exactly those full segmentation would produce."""
    expected = _expected_cells(text)
    # Comfortably wider than the content, so this is about segmentation only.
    rows = _render(text, max(24, len(expected) * 2 + 12))
    actual = rows[0] if rows else []

    assert actual[: len(expected)] == expected, f"segmentation diverged for {text!r}"
    # Everything after the content is padding, never stray glyphs.
    assert set(actual[len(expected) :]) <= {" "}, f"unexpected trailing cells for {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "the quick brown fox jumps over the lazy dog again and again",
        "日本語のテキストが折り返されるときの挙動を確認する",
        "mixed 日本 text with \U0001f600 emoji that needs to wrap somewhere",
        "a\u0301" * 40,
    ],
)
def test_content_survives_wrapping_at_narrow_widths(text: str) -> None:
    """Resize re-wraps at a new width, so the fast path must hold there too.

    Leading whitespace is excluded from this corpus: continuation rows re-apply
    the source indent, which would legitimately add cells.
    """
    expected = [cell for cell in _expected_cells(text) if cell != " "]
    for width in (8, 13, 20, 31):
        rows = _render(text, width)
        got = [cell for row in rows for cell in row if cell != " "]
        assert got == expected, f"content changed wrapping {text!r} at width {width}"


def test_keycap_digit_is_not_split_by_the_ascii_fast_path() -> None:
    """The specific regression the guard exists to prevent.

    "1" is printable ASCII, so a naive fast path would emit it as a standalone
    one-column cluster and orphan the U+FE0F/U+20E3 that follow it.
    """
    row = _render("1\ufe0f\u20e3", 20)[0]
    assert row[0] == "1\ufe0f\u20e3"
    assert row[1] == " "  # double-width continuation


def test_combining_accent_stays_attached_to_ascii_base() -> None:
    row = _render("a\u0301bc", 20)[0]
    assert row[:3] == ["a\u0301", "b", "c"]


def test_ascii_before_non_ascii_is_not_merged() -> None:
    """The guard is conservative, not greedy: 'i' followed by a non-ASCII char
    takes the slow path, which must still emit 'i' as its own cluster."""
    row = _render("hi日", 20)[0]
    assert row[:4] == ["h", "i", "日", " "]


def test_styles_still_track_across_the_fast_path() -> None:
    """The fast path must carry the active SGR style, not reset it."""
    buf = Buffer.empty(Rect(0, 0, 20, 0))
    parse_ansi_wrapped_into(buf, 0, 0, "\x1b[1mAB\x1b[0mC", 20)
    assert buf.get(0, 0).style.add_modifier
    assert buf.get(1, 0).style.add_modifier
    assert not buf.get(2, 0).style.add_modifier
