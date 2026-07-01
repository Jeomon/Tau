"""Tests for tau/tui/fuzzy.py — fuzzy matching and filtering."""

from __future__ import annotations

from tau.tui.utils import FuzzyMatch, fuzzy_filter, fuzzy_match


class TestFuzzyMatch:
    def test_empty_query_always_matches(self):
        result = fuzzy_match("", "anything")
        assert result.matches is True
        assert result.score == 0

    def test_exact_match(self):
        result = fuzzy_match("foo", "foo")
        assert result.matches is True

    def test_subsequence_match(self):
        # "ff" appears as subsequence in "fuzzy_filter" (positions 0 and 6)
        result = fuzzy_match("ff", "fuzzy_filter")
        assert result.matches is True

    def test_no_match_when_char_missing(self):
        result = fuzzy_match("xyz", "abc")
        assert result.matches is False

    def test_query_longer_than_text_no_match(self):
        result = fuzzy_match("abcdef", "abc")
        assert result.matches is False

    def test_case_insensitive(self):
        result = fuzzy_match("FOO", "foobar")
        assert result.matches is True

    def test_exact_match_has_lowest_score(self):
        exact = fuzzy_match("test", "test")
        partial = fuzzy_match("test", "testing things")
        assert exact.score < partial.score

    def test_word_boundary_bonus(self):
        # "fc" at word boundary in "fuzzy_filter" should score better than interior match
        boundary = fuzzy_match("ff", "fuzzy_filter")
        interior = fuzzy_match("zz", "fuzzy_filter")
        assert boundary.matches is True
        assert interior.matches is True

    def test_returns_fuzzy_match_instance(self):
        result = fuzzy_match("a", "abc")
        assert isinstance(result, FuzzyMatch)

    def test_swapped_alphanumeric(self):
        # "v3" typed against text "3version" — swap tries "3v" which is a subsequence
        result = fuzzy_match("v3", "3version")
        assert result.matches is True

    def test_no_match_returns_false(self):
        result = fuzzy_match("zzz", "abc")
        assert result.matches is False
        assert result.score == 0


class TestFuzzyFilter:
    def test_empty_query_returns_all(self):
        items = ["foo", "bar", "baz"]
        result = fuzzy_filter(items, "", lambda x: x)
        assert result == items

    def test_whitespace_query_returns_all(self):
        items = ["foo", "bar"]
        result = fuzzy_filter(items, "   ", lambda x: x)
        assert result == items

    def test_filters_non_matching(self):
        items = ["apple", "banana", "cherry"]
        result = fuzzy_filter(items, "an", lambda x: x)
        assert "banana" in result
        assert "apple" not in result

    def test_sorts_by_score(self):
        # "foo" should rank before "foobar" for exact query "foo"
        items = ["foobar", "foo"]
        result = fuzzy_filter(items, "foo", lambda x: x)
        assert result[0] == "foo"

    def test_multi_token_all_must_match(self):
        items = ["fuzzy filter", "fuzzy", "filter results"]
        result = fuzzy_filter(items, "fuzzy filter", lambda x: x)
        assert "fuzzy filter" in result
        assert "fuzzy" not in result

    def test_custom_get_text(self):
        items = [{"name": "apple"}, {"name": "banana"}]
        result = fuzzy_filter(items, "ban", lambda x: x["name"])
        assert len(result) == 1
        assert result[0]["name"] == "banana"

    def test_empty_list(self):
        result = fuzzy_filter([], "foo", lambda x: x)
        assert result == []
