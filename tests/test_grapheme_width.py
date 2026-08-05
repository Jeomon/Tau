"""visible_width must measure grapheme clusters, not codepoints.

Terminal width is a property of whole user-perceived characters. A ZWJ family
emoji is five codepoints but occupies two columns; a combining accent occupies
none of its own. Measuring per codepoint reported the family emoji as six
columns, which wraps lines in the wrong place — and becomes load-bearing once
wrapping is done on strings rather than a cell grid.

Segmenting every string is 11-160x slower, so cluster-forming codepoints are
detected first with a compiled regex. These tests pin both halves: the answers
are right, and text with no cluster-forming codepoints still takes the fast
per-codepoint path.
"""

from __future__ import annotations

import grapheme
import pytest

from tau.tui.ansi_bridge import grapheme_width
from tau.tui.utils import strip_ansi, visible_width


def reference_width(text: str) -> int:
    """Ground truth: sum the width of each grapheme cluster."""
    stripped = strip_ansi(text) if "\x1b" in text else text
    return sum(grapheme_width(c) for c in grapheme.graphemes(stripped))


CASES = [
    ("ascii", "line 1234 of tool output, path/to/file.py"),
    ("cjk", "日本語のテキスト → ★"),
    ("precomposed latin", "café naïve résumé"),
    ("zwj family", "👨\u200d👩\u200d👧"),
    ("zwj in a sentence", "🎉 party 👨\u200d👩\u200d👧 family"),
    ("regional flag", "🇯🇵"),
    ("several flags", "🇯🇵🇺🇸🇩🇪"),
    ("skin tone", "👍🏽"),
    ("combining accents", "e\u0301 a\u0300 o\u0302"),
    ("keycap", "1\ufe0f\u20e3"),
    ("variation selector", "❤\ufe0f"),
    ("styled emoji", "\x1b[31m🎉\x1b[0m 👨\u200d👩\u200d👧"),
    ("empty", ""),
    ("spaces", "    "),
]


@pytest.mark.parametrize(("name", "text"), CASES, ids=[c[0] for c in CASES])
def test_visible_width_matches_grapheme_segmentation(name: str, text: str) -> None:
    assert visible_width(text) == reference_width(text)


def test_zwj_family_is_two_columns_not_six() -> None:
    """The specific regression: five codepoints, one cluster, two columns."""
    family = "👨\u200d👩\u200d👧"
    assert len(family) == 5
    assert visible_width(family) == 2


def test_combining_accent_adds_no_width() -> None:
    assert visible_width("e\u0301") == visible_width("e") == 1


def _segmentation_spy(monkeypatch):
    """Record whether the (slow) cluster-segmentation path was taken."""
    seen = {"called": False}
    original = grapheme.graphemes

    def spy(text):
        seen["called"] = True
        return original(text)

    monkeypatch.setattr("tau.tui.utils.grapheme.graphemes", spy)
    return seen


def test_ascii_still_takes_the_fast_path(monkeypatch) -> None:
    """Correctness must not cost segmentation on text that cannot need it."""
    seen = _segmentation_spy(monkeypatch)
    assert visible_width("plain ascii output line") == 23
    assert seen["called"] is False


def test_cjk_still_takes_the_fast_path(monkeypatch) -> None:
    """CJK has no cluster-forming codepoints, so per-codepoint is already right."""
    seen = _segmentation_spy(monkeypatch)
    assert visible_width("\u65e5\u672c\u8a9e\u306e\u30c6\u30ad\u30b9\u30c8") == 16
    assert seen["called"] is False


def test_emoji_does_take_the_slow_path(monkeypatch) -> None:
    seen = _segmentation_spy(monkeypatch)
    visible_width("\U0001f468\u200d\U0001f469\u200d\U0001f467")
    assert seen["called"] is True
