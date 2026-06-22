"""Tests for tau/tui/ansi.py — ANSI escape code utilities."""
from __future__ import annotations

from tau.tui.ansi import (
    BLUE,
    BOLD,
    DIM,
    ITALIC,
    RED,
    RESET,
    UNDERLINE,
    _AnsiStateTracker,
    _char_width,
    bg,
    bg256,
    fg,
    fg256,
    pad,
    strip_ansi,
    style,
    truncate,
    visible_width,
    wrap,
)


class TestColorGenerators:
    def test_fg_truecolor(self):
        assert fg(255, 0, 128) == "\x1b[38;2;255;0;128m"

    def test_bg_truecolor(self):
        assert bg(0, 128, 255) == "\x1b[48;2;0;128;255m"

    def test_fg256(self):
        assert fg256(42) == "\x1b[38;5;42m"

    def test_bg256(self):
        assert bg256(200) == "\x1b[48;5;200m"

    def test_style_wraps_with_reset(self):
        result = style("hello", BOLD, RED)
        assert result.startswith(BOLD + RED)
        assert result.endswith(RESET)
        assert "hello" in result

    def test_style_single_code(self):
        result = style("x", BLUE)
        assert BLUE in result
        assert "x" in result
        assert result.endswith(RESET)


class TestStripAnsi:
    def test_strips_sgr(self):
        assert strip_ansi(f"{BOLD}hello{RESET}") == "hello"

    def test_strips_color(self):
        assert strip_ansi(f"{RED}red{RESET}") == "red"

    def test_plain_text_unchanged(self):
        assert strip_ansi("plain") == "plain"

    def test_empty_string(self):
        assert strip_ansi("") == ""

    def test_multiple_codes(self):
        text = f"{BOLD}{RED}bold red{RESET}"
        assert strip_ansi(text) == "bold red"

    def test_truecolor(self):
        text = f"{fg(255, 0, 0)}red{RESET}"
        assert strip_ansi(text) == "red"


class TestVisibleWidth:
    def test_ascii_string(self):
        assert visible_width("hello") == 5

    def test_empty_string(self):
        assert visible_width("") == 0

    def test_ansi_codes_not_counted(self):
        assert visible_width(f"{BOLD}abc{RESET}") == 3

    def test_wide_cjk_chars(self):
        # CJK characters are 2 columns wide
        assert visible_width("日本") == 4

    def test_mixed_ascii_and_cjk(self):
        assert visible_width("a日") == 3


class TestCharWidth:
    def test_ascii_letter(self):
        assert _char_width("a") == 1

    def test_space(self):
        assert _char_width(" ") == 1

    def test_cjk_wide(self):
        assert _char_width("中") == 2

    def test_null_byte(self):
        assert _char_width("\x00") == 0


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_truncates_long_text(self):
        result = truncate("hello world", 7)
        assert visible_width(result) <= 7
        assert "…" in strip_ansi(result)

    def test_exact_width_unchanged(self):
        text = "hello"
        assert truncate(text, 5) == text

    def test_truncates_ansi_text(self):
        text = f"{RED}hello world{RESET}"
        result = truncate(text, 7)
        assert visible_width(result) <= 7


class TestPad:
    def test_left_pad(self):
        result = pad("hi", 5)
        assert result == "hi   "
        assert visible_width(result) == 5

    def test_right_pad(self):
        result = pad("hi", 5, align="right")
        assert result == "   hi"

    def test_center_pad(self):
        result = pad("hi", 6, align="center")
        assert visible_width(result) == 6
        assert "hi" in result

    def test_no_padding_needed(self):
        result = pad("hello", 3)
        assert result == "hello"

    def test_custom_char(self):
        result = pad("hi", 5, char="-")
        assert result == "hi---"


class TestWrap:
    def test_short_line_not_wrapped(self):
        assert wrap("hello", 80) == ["hello"]

    def test_wraps_long_line(self):
        lines = wrap("a" * 10, 5)
        assert len(lines) == 2
        for line in lines:
            assert visible_width(strip_ansi(line)) <= 5

    def test_respects_newlines(self):
        lines = wrap("line1\nline2", 80)
        assert len(lines) == 2

    def test_zero_width_returns_text(self):
        result = wrap("hello", 0)
        assert result == ["hello"]

    def test_wraps_with_ansi(self):
        text = f"{RED}{'a' * 12}{RESET}"
        lines = wrap(text, 5)
        assert len(lines) > 1
        for line in lines:
            assert visible_width(line) <= 5

    def test_empty_string(self):
        assert wrap("", 10) == [""]

    def test_all_wrapped_lines_fit_width(self):
        long = "x" * 200
        lines = wrap(long, 40)
        for ln in lines:
            assert visible_width(ln) <= 40

    def test_content_preserved_across_wrap(self):
        long = "a" * 150
        lines = wrap(long, 50)
        assert "".join(lines) == long


class _FakeExpander:
    """Mirrors the renderer's per-frame overflow-expand logic for unit testing."""

    def __init__(self, width: int) -> None:
        self._width = width
        self._cache: dict[str, list[str]] = {}

    def expand(self, logical_lines: list[str]) -> list[str]:
        physical: list[str] = []
        new_cache: dict[str, list[str]] = {}
        for line in logical_lines:
            cached = self._cache.get(line)
            if cached is None:
                if visible_width(line) > self._width:
                    cached = wrap(line, self._width)
                    new_cache[line] = cached
                    physical.extend(cached)
                else:
                    physical.append(line)
                continue
            new_cache[line] = cached
            physical.extend(cached)
        self._cache = new_cache
        return physical


class TestRendererOverflowWrap:
    """Tests for the renderer's overflow-wrap logic (mirrors Renderer._clamp_cache path)."""

    def test_short_line_passes_through(self):
        exp = _FakeExpander(80)
        assert exp.expand(["hello"]) == ["hello"]

    def test_overflow_line_expanded_not_truncated(self):
        exp = _FakeExpander(10)
        result = exp.expand(["a" * 25])
        assert len(result) > 1
        assert "".join(result) == "a" * 25

    def test_expanded_lines_fit_width(self):
        exp = _FakeExpander(20)
        result = exp.expand(["x" * 55])
        for ln in result:
            assert visible_width(ln) <= 20

    def test_non_overflow_line_not_cached(self):
        exp = _FakeExpander(80)
        exp.expand(["short"])
        assert "short" not in exp._cache

    def test_overflow_line_is_cached(self):
        exp = _FakeExpander(10)
        line = "a" * 30
        exp.expand([line])
        assert line in exp._cache

    def test_cache_hit_returns_same_result(self):
        exp = _FakeExpander(10)
        line = "b" * 30
        first = exp.expand([line])
        second = exp.expand([line])
        assert first == second

    def test_width_change_clears_cache(self):
        exp = _FakeExpander(10)
        line = "c" * 30
        exp.expand([line])
        # Simulate width change by creating a new expander (renderer clears cache on width change)
        exp2 = _FakeExpander(20)
        result = exp2.expand([line])
        # At width=20 fewer lines needed than at width=10
        assert len(result) <= len(exp._cache[line])

    def test_mixed_lines(self):
        exp = _FakeExpander(10)
        result = exp.expand(["hi", "x" * 25, "ok"])
        assert result[0] == "hi"
        assert result[-1] == "ok"
        assert "x" * 25 == "".join(ln for ln in result if ln not in ("hi", "ok"))


class TestAnsiStateTracker:
    def test_initially_no_state(self):
        t = _AnsiStateTracker()
        assert t.has_state() is False
        assert t.active_codes() == ""

    def test_bold_sets_state(self):
        t = _AnsiStateTracker()
        t.process(BOLD)
        assert t.has_state() is True
        assert BOLD in t.active_codes()

    def test_reset_clears_state(self):
        t = _AnsiStateTracker()
        t.process(BOLD)
        t.process(RESET)
        assert t.has_state() is False

    def test_fg_color_tracked(self):
        t = _AnsiStateTracker()
        t.process(RED)
        assert t.has_state() is True
        assert RED in t.active_codes()

    def test_italic_tracked(self):
        t = _AnsiStateTracker()
        t.process(ITALIC)
        assert t.has_state() is True

    def test_underline_tracked(self):
        t = _AnsiStateTracker()
        t.process(UNDERLINE)
        assert t.has_state() is True

    def test_dim_tracked(self):
        t = _AnsiStateTracker()
        t.process(DIM)
        assert t.has_state() is True

    def test_multiple_codes_accumulated(self):
        t = _AnsiStateTracker()
        t.process(BOLD)
        t.process(RED)
        codes = t.active_codes()
        assert BOLD in codes
        assert RED in codes

    def test_non_sgr_code_ignored(self):
        t = _AnsiStateTracker()
        t.process("\x1b[2J")  # clear screen, not SGR
        assert t.has_state() is False

    def test_truecolor_fg_tracked(self):
        t = _AnsiStateTracker()
        t.process(fg(100, 200, 50))
        assert t.has_state() is True
        codes = t.active_codes()
        assert "38;2" in codes
