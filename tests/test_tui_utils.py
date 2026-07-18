"""Tests for tau/tui/utils.py — project_name."""

from __future__ import annotations

from tau.tui.utils import clip_to_width, grapheme_width, project_name


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
