"""``ansi_text.tokenize`` has an ASCII fast path; this pins the reasoning behind it.

The fast path skips grapheme segmentation for a printable-ASCII character
followed by another printable-ASCII character (or end of line), on the reasoning
that nothing which *combines* is ASCII. This module pins that reasoning by
comparing against full segmentation rather than trusting it — the dangerous
cases are ASCII characters that do form part of a longer cluster, e.g. the digit
in a keycap ("1\ufe0f\u20e3") or a base letter carrying a combining accent.
"""

from __future__ import annotations

import grapheme
import pytest

from tau.tui.ansi_text import tokenize, wrap_ansi
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


def _expected_clusters(text: str) -> list[str]:
    """Segment ``text`` with full grapheme segmentation — the reference."""
    out: list[str] = []
    for raw in grapheme.graphemes(text):
        cluster = raw or ""
        if grapheme_width(cluster) == 0:
            continue
        out.append(cluster)
    return out


def _clusters(text: str) -> list[str]:
    return [cluster for cluster, _width, _style in tokenize(text)]


@pytest.mark.parametrize("text", CORPUS)
def test_fast_path_matches_full_grapheme_segmentation(text: str) -> None:
    """Clusters emitted must be exactly those full segmentation would produce."""
    assert _clusters(text) == _expected_clusters(text), f"segmentation diverged for {text!r}"


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
    the source indent, which would legitimately add clusters.
    """
    expected = [c for c in _expected_clusters(text) if not c.isspace()]
    for width in (8, 13, 20, 31):
        got = [c for row in wrap_ansi(text, width) for c in _clusters(row) if not c.isspace()]
        assert got == expected, f"content changed wrapping {text!r} at width {width}"


def test_keycap_digit_is_not_split_by_the_ascii_fast_path() -> None:
    """The specific regression the guard exists to prevent.

    "1" is printable ASCII, so a naive fast path would emit it as a standalone
    one-column cluster and orphan the U+FE0F/U+20E3 that follow it.
    """
    tokens = tokenize("1\ufe0f\u20e3")
    assert [t[0] for t in tokens] == ["1\ufe0f\u20e3"]
    assert tokens[0][1] == 2  # keycap is double-width


def test_combining_accent_stays_attached_to_ascii_base() -> None:
    assert _clusters("a\u0301bc") == ["a\u0301", "b", "c"]


def test_ascii_before_non_ascii_is_not_merged() -> None:
    """The guard is conservative, not greedy: 'i' followed by a non-ASCII char
    takes the slow path, which must still emit 'i' as its own cluster."""
    assert _clusters("hi日") == ["h", "i", "日"]


def test_styles_still_track_across_the_fast_path() -> None:
    """The fast path must carry the active SGR style, not reset it."""
    tokens = tokenize("\x1b[1mAB\x1b[0mC")
    assert [t[0] for t in tokens] == ["A", "B", "C"]
    assert tokens[0][2].add_modifier
    assert tokens[1][2].add_modifier
    assert not tokens[2][2].add_modifier
