"""LaTeX math extraction and conversion (tau/tui/latex.py).

The rendered-output side is covered through ``render_markdown`` in
test_markdown.py; this exercises the module's own seam — the extract/restore
round trip that markdown.py depends on, and the placeholder staying private to
this module.
"""

from __future__ import annotations

from tau.tui.latex import extract_math, math_to_text, restore_math


class TestMathToText:
    def test_converts_a_simple_expression(self):
        assert math_to_text("E = mc^2") == "E = mc²"

    def test_converts_braced_scripts(self):
        assert "ᵢⱼ" in math_to_text("x_{ij}")

    def test_spaces_relations(self):
        assert math_to_text("a=b") == "a = b"

    def test_input_it_cannot_convert_comes_back_as_source(self):
        """pylatexenc is lenient — it echoes what it cannot interpret rather
        than raising, so the fallback is 'unchanged text', not an error."""
        assert math_to_text(r"\begin{matrix}") == r"\begin{matrix}"

    def test_a_truncated_macro_is_converted_as_far_as_it_parses(self):
        """Not an error path: an unclosed brace still yields readable output."""
        assert math_to_text(r"\frac{1}{") == "1/"


class TestExtractAndRestore:
    def test_a_span_is_replaced_and_restored(self):
        stripped, replacements = extract_math("Inline $E = mc^2$ here.")

        assert "$" not in stripped
        assert replacements == ["E = mc²"]
        assert restore_math(stripped, replacements) == "Inline E = mc² here."

    def test_display_math_is_restored_on_its_own_lines(self):
        stripped, replacements = extract_math("Before $$a = b$$ after")

        assert replacements[0].startswith("\n")
        assert replacements[0].endswith("\n")
        assert restore_math(stripped, replacements) == "Before \na = b\n after"

    def test_paren_and_bracket_delimiters(self):
        for source in (r"\(a^2\)", r"\[a^2\]"):
            _, replacements = extract_math(source)
            assert replacements, source

    def test_several_spans_keep_their_order(self):
        stripped, replacements = extract_math("$a$ then $b$")

        assert len(replacements) == 2
        assert restore_math(stripped, replacements) == "a then b"

    def test_code_spans_and_fences_are_left_alone(self):
        for source in ("Use `$not math$` here", "```tex\n$\\alpha$\n```"):
            stripped, replacements = extract_math(source)
            assert replacements == [], source
            assert stripped == source, source

    def test_currency_is_not_math(self):
        stripped, replacements = extract_math("Costs $5 and $10.")

        assert replacements == []
        assert stripped == "Costs $5 and $10."

    def test_text_without_math_is_unchanged(self):
        assert extract_math("just prose") == ("just prose", [])

    def test_restore_is_a_no_op_without_replacements(self):
        assert restore_math("nothing to do", []) == "nothing to do"

    def test_the_placeholder_does_not_leak_into_restored_text(self):
        """markdown.py never sees the marker's shape — it holds the list only."""
        stripped, replacements = extract_math("Inline $x^2$ here.")
        restored = restore_math(stripped, replacements)

        assert "\ue000" in stripped
        assert "\ue000" not in restored
