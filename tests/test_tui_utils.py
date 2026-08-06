"""Tests for tau/tui/utils.py — project_name."""

from __future__ import annotations

import re

from tau.tui.style import OSC8_CLOSE
from tau.tui.utils import (
    clip_to_width,
    grapheme_width,
    project_name,
    slice_columns,
    truncate,
    truncate_to_width,
)

# Deliberately a separate pattern from the one in tau.tui.utils: a probe that
# shares the implementation's regex would hide a bug in that regex.
_OSC8_PROBE = re.compile(r"\x1b\]8;[^;]*;([^\x07\x1b]*)")


class TestProjectName:
    def test_returns_string(self):
        result = project_name()
        assert isinstance(result, str)

    def test_nonempty(self):
        assert len(project_name()) > 0

    def test_returns_cwd_name_when_no_git(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No .git directory — subprocess will fail or return non-zero
        result = project_name()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_in_git_repo_returns_basename(self, monkeypatch):
        import subprocess

        class _GitResult:
            returncode = 0
            stdout = "/home/user/my-project\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _GitResult())
        result = project_name()
        assert result == "my-project"
        assert "/" not in result
        assert "\\" not in result


class TestGraphemeWidth:
    """Cluster width must reflect how the sequence renders, not just its first codepoint."""

    def test_ascii(self):
        assert grapheme_width("a") == 1

    def test_cjk(self):
        assert grapheme_width("\u4e2d") == 2

    def test_combining_accent_cluster(self):
        assert grapheme_width("e\u0301") == 1

    def test_flag_regional_indicator_pair(self):
        assert grapheme_width("\U0001f1fa\U0001f1f8") == 2  # US flag

    def test_keycap_sequence(self):
        assert grapheme_width("1\ufe0f\u20e3") == 2  # keycap digit one

    def test_emoji_presentation_vs16(self):
        assert grapheme_width("\u2764\ufe0f") == 2  # red heart

    def test_zwj_sequence(self):
        assert grapheme_width("\U0001f469\u200d\U0001f4bb") == 2  # woman technologist

    def test_empty(self):
        assert grapheme_width("") == 0


class TestClipToWidth:
    def test_short_text_unchanged(self):
        assert clip_to_width("abc", 5) == "abc"

    def test_ascii_clipped(self):
        assert clip_to_width("abcdef", 3) == "abc"

    def test_cjk_clipped_on_column_boundary(self):
        assert clip_to_width("\u65e5\u672c\u8a9e", 4) == "\u65e5\u672c"

    def test_cjk_never_overflows_odd_width(self):
        assert clip_to_width("\u65e5\u672c\u8a9e", 3) == "\u65e5"

    def test_zero_width_returns_empty(self):
        assert clip_to_width("abc", 0) == ""


class TestTruncationClosesHyperlinks:
    """Cutting through an OSC 8 label must not leave the hyperlink open.

    The terminal keeps applying an open hyperlink to everything printed after
    it — the ellipsis, the rest of the row, the next line — so a truncated link
    turns the remainder of the screen into one clickable region. An SGR reset
    does not end a hyperlink; only the OSC 8 terminator does, which is why
    ``truncate``'s trailing RESET was not already enough.
    """

    LINK = "\x1b]8;;https://example.com/docs\x1b\\Tau documentation\x1b]8;;\x1b\\"
    TEXT = f"See {LINK} for details"

    @staticmethod
    def _link_open(text: str) -> bool:
        """True when the last OSC 8 sequence in ``text`` opens rather than closes."""
        targets = _OSC8_PROBE.findall(text)
        return bool(targets and targets[-1])

    def test_the_probe_catches_an_unclosed_link(self):
        """Guard the guard: the helper must actually detect the bug condition."""
        assert self._link_open("\x1b]8;;https://x.com\x1b\\label")
        assert not self._link_open(self.TEXT)
        assert not self._link_open("no links here")

    def test_truncate_to_width_closes_a_cut_link(self):
        assert not self._link_open(truncate_to_width(self.TEXT, 12))

    def test_truncate_closes_a_cut_link_before_the_ellipsis(self):
        out = truncate(self.TEXT, 12)
        assert not self._link_open(out)
        # The ellipsis must fall outside the link, not inside it.
        assert out.index(OSC8_CLOSE) < out.index("…")

    def test_clip_to_width_closes_a_cut_link(self):
        assert not self._link_open(clip_to_width(self.TEXT, 12))

    def test_slice_columns_closes_a_window_opened_inside_a_link(self):
        """The window re-emits the opening sequence, so it owns the close."""
        out = slice_columns(self.TEXT, 6, 14)
        assert "\x1b]8;;https://example.com/docs" in out
        assert not self._link_open(out)

    def test_text_that_is_not_truncated_is_unchanged(self):
        assert truncate_to_width(self.TEXT, 100) == self.TEXT
        assert truncate(self.TEXT, 100) == self.TEXT
        assert clip_to_width(self.TEXT, 100) == self.TEXT

    def test_no_close_is_added_when_the_cut_falls_after_the_link(self):
        for out in (truncate_to_width(self.TEXT, 24), clip_to_width(self.TEXT, 24)):
            assert not self._link_open(out)
            assert not out.endswith(OSC8_CLOSE)

    def test_plain_text_is_unaffected(self):
        assert truncate_to_width("just some ordinary text", 9) == "just some"
        assert OSC8_CLOSE not in truncate("just some ordinary text", 9)

    def test_a_link_with_params_is_handled(self):
        """OSC 8 allows params before the target; Tau emits none but tools may."""
        text = "\x1b]8;id=1;https://example.com\x1b\\Some label\x1b]8;;\x1b\\ tail"

        assert not self._link_open(truncate_to_width(text, 6))
