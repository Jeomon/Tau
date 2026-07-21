"""Tests for tau.tui.utils.rule — the shared horizontal rule.

Ten call sites used to hardcode ``"─" * area.width``. These pin the helper to
be an exact substitution for both forms that existed: the pre-styled ANSI
string, and the bare glyph run whose caller carries the style.
"""

from __future__ import annotations

from tau.tui.style import Style, apply_style
from tau.tui.utils import rule, visible_width
from tau.tui.widgets.symbols import DOUBLE, PLAIN, THICK


class TestExactSubstitution:
    def test_bare_form_matches_the_old_literal(self):
        for width in (0, 1, 7, 80):
            assert rule(width) == "─" * width

    def test_styled_form_matches_the_old_expression(self):
        style = Style().with_fg("bright_black")
        for width in (1, 40):
            assert rule(width, style) == apply_style(style, "─" * width)


class TestWidth:
    def test_occupies_exactly_the_requested_columns(self):
        assert visible_width(rule(37)) == 37
        assert visible_width(rule(37, Style().with_fg("red"))) == 37

    def test_negative_and_zero_widths_are_empty(self):
        assert rule(0) == ""
        assert rule(-5) == ""


class TestBorderSets:
    def test_defaults_to_the_plain_set(self):
        assert rule(3) == PLAIN.horizontal * 3

    def test_other_sets_swap_the_glyph(self):
        assert rule(3, border_set=DOUBLE) == "═══"
        assert rule(3, border_set=THICK) == "━━━"

    def test_style_and_border_set_compose(self):
        style = Style().with_fg("red")
        assert rule(2, style, DOUBLE) == apply_style(style, "══")
