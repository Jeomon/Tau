"""Tests for tau/tui/diff.py — unified diff rendering."""

from __future__ import annotations

from tau.tui.utils import _is_diff, _word_diff, render_diff


# Identity styling functions for test assertions
def _id(s: str) -> str:
    return s


def _tag(prefix: str):
    return lambda s: f"[{prefix}]{s}[/{prefix}]"


class TestIsDiff:
    def test_recognises_unified_diff(self):
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
        assert _is_diff(diff) is True

    def test_plain_text_is_not_diff(self):
        assert _is_diff("Hello world\nThis is text") is False

    def test_only_context_no_change_is_not_diff(self):
        # has no +/- change lines and no @@ / +++ markers
        assert _is_diff("context line one\ncontext line two") is False

    def test_empty_string_is_not_diff(self):
        assert _is_diff("") is False

    def test_change_lines_without_markers_is_not_diff(self):
        assert _is_diff("+added\n-removed") is False


class TestWordDiff:
    def test_identical_lines_not_highlighted(self):
        old, new = _word_diff("hello world", "hello world", lambda s: f"[{s}]")
        assert old == "hello world"
        assert new == "hello world"

    def test_single_word_changed(self):
        old, new = _word_diff("hello world", "hello earth", lambda s: f"[{s}]")
        assert "world" in old
        assert "earth" in new
        # Changed words get highlighted
        assert "[world]" in old
        assert "[earth]" in new

    def test_empty_lines(self):
        old, new = _word_diff("", "", _id)
        assert old == ""
        assert new == ""

    def test_added_word(self):
        _old, new = _word_diff("hello", "hello world", lambda s: f"[{s}]")
        # "world" is inserted — some form of highlighting appears in new
        assert "[" in new and "world" in new

    def test_deleted_word(self):
        old, _new = _word_diff("hello world", "hello", lambda s: f"[{s}]")
        # "world" is deleted — some form of highlighting appears in old
        assert "[" in old and "world" in old


class TestRenderDiff:
    def _make_fns(self):
        return (
            _tag("add"),
            _tag("rem"),
            _tag("ctx"),
            _tag("hunk"),
            _tag("inv"),
        )

    def test_hunk_header_styled(self):
        diff = "@@ -1,2 +1,2 @@"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert len(result) == 1
        assert "[hunk]" in result[0]

    def test_plus_minus_header_styled(self):
        diff = "--- a/f.py\n+++ b/f.py"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert all("[hunk]" in line for line in result)

    def test_added_line_styled(self):
        diff = "+new line"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert "[add]" in result[0]

    def test_removed_line_styled(self):
        diff = "-old line"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert "[rem]" in result[0]

    def test_context_line_styled(self):
        diff = " context line"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert "[ctx]" in result[0]

    def test_one_to_one_change_uses_word_diff(self):
        diff = "-old word here\n+new word here"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        # Word diff: "old" and "new" should be highlighted with inverse
        assert "[inv]" in result[0] or "[inv]" in result[1]

    def test_many_to_one_no_word_diff(self):
        diff = "-line one\n-line two\n+single new line"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        assert len(result) == 3

    def test_empty_diff(self):
        result = render_diff("", _id, _id, _id, _id, _id)
        assert result == []

    def test_full_diff(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n unchanged\n-old\n+new\n"
        added, removed, context, hunk, inverse = self._make_fns()
        result = render_diff(diff, added, removed, context, hunk, inverse)
        # 3 headers + 1 context + 2 change lines (word-diff of 1:1 replace)
        assert len(result) == 6
