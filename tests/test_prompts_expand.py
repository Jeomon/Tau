"""Tests for tau/prompts/expand.py — template argument expansion."""

from __future__ import annotations

from tau.prompts.expand import _parse_args, expand


class TestParseArgs:
    def test_empty_string_returns_empty(self):
        assert _parse_args("") == []

    def test_splits_whitespace(self):
        assert _parse_args("a b c") == ["a", "b", "c"]

    def test_preserves_quoted_strings(self):
        assert _parse_args('"hello world" foo') == ["hello world", "foo"]

    def test_single_word(self):
        assert _parse_args("hello") == ["hello"]

    def test_unclosed_quote_falls_back(self):
        result = _parse_args('"unclosed')
        assert isinstance(result, list)


class TestExpand:
    def test_no_placeholders_returned_unchanged(self):
        assert expand("Hello world", "") == "Hello world"

    def test_positional_arg_dollar_1(self):
        assert expand("Hello $1", "Alice") == "Hello Alice"

    def test_positional_arg_dollar_2(self):
        assert expand("$1 and $2", "foo bar") == "foo and bar"

    def test_out_of_range_positional_returns_empty(self):
        assert expand("$3", "a b") == ""

    def test_dollar_ARGUMENTS_all_args(self):
        assert expand("$ARGUMENTS", "one two") == "one two"

    def test_dollar_ARGUMENTS_no_args_is_empty(self):
        assert expand("[$ARGUMENTS]", "") == "[]"

    def test_brace_positional(self):
        assert expand("${1}", "hello") == "hello"

    def test_brace_with_default_used(self):
        assert expand("${1:-default}", "") == "default"

    def test_brace_with_default_not_used_when_arg_provided(self):
        assert expand("${1:-default}", "actual") == "actual"

    def test_brace_slice_from_n(self):
        assert expand("${@:2}", "a b c d") == "b c d"

    def test_brace_slice_with_length(self):
        assert expand("${@:2:2}", "a b c d") == "b c"

    def test_multiple_placeholders(self):
        result = expand("$1 $2 $ARGUMENTS", "hello world")
        assert result == "hello world hello world"

    def test_quoted_arg_with_space(self):
        result = expand("$1", '"hello world"')
        assert result == "hello world"

    def test_unknown_brace_pattern_unchanged(self):
        result = expand("${unknown}", "arg")
        assert result == "${unknown}"
