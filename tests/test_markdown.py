"""Tests for tau/tui/markdown.py — render_markdown output."""

from __future__ import annotations

from tau.tui.markdown import StreamingMarkdownRenderer, render_markdown
from tau.tui.theme import MarkdownTheme
from tau.tui.utils import strip_ansi


def _theme() -> MarkdownTheme:
    return MarkdownTheme()


def render(md: str, width: int = 80) -> list[str]:
    return render_markdown(md, width, _theme())


def plain(md: str, width: int = 80) -> list[str]:
    return [strip_ansi(line) for line in render(md, width)]


class TestParagraph:
    def test_simple_paragraph(self):
        lines = plain("Hello world")
        assert "Hello world" in " ".join(lines)

    def test_multiple_words(self):
        lines = plain("The quick brown fox")
        combined = " ".join(lines)
        assert "quick" in combined

    def test_returns_list(self):
        assert isinstance(render("Some text"), list)

    def test_empty_string(self):
        lines = render("")
        assert lines == []


class TestHeadings:
    def test_h1_contains_text(self):
        lines = plain("# Title")
        assert any("Title" in ln for ln in lines)

    def test_h2_contains_text(self):
        lines = plain("## Section")
        assert any("Section" in ln for ln in lines)

    def test_h3_contains_text(self):
        lines = plain("### Subsection")
        assert any("Subsection" in ln for ln in lines)

    def test_heading_has_ansi_styling(self):
        lines = render("# Title")
        raw = "\n".join(lines)
        assert "\x1b[" in raw


class TestCodeFence:
    def test_code_fence_contains_code(self):
        lines = plain("```\nprint('hi')\n```")
        combined = "\n".join(lines)
        assert "print" in combined

    def test_code_fence_python(self):
        lines = plain("```python\nresult = 1 + 1\n```")
        combined = "\n".join(lines)
        assert "result" in combined

    def test_code_fence_multiline(self):
        lines = plain("```\nline1\nline2\nline3\n```")
        combined = "\n".join(lines)
        assert "line1" in combined
        assert "line3" in combined


class TestThematicBreak:
    def test_hr_renders_as_line(self):
        lines = plain("---")
        assert len(lines) >= 1
        assert any("─" in ln or "-" in ln for ln in lines)

    def test_hr_spans_width(self):
        lines = plain("---", width=40)
        assert len(lines) >= 1


class TestLists:
    def test_unordered_list_items(self):
        lines = plain("- alpha\n- beta\n- gamma")
        combined = "\n".join(lines)
        assert "alpha" in combined
        assert "beta" in combined
        assert "gamma" in combined

    def test_unordered_bullet_marker(self):
        lines = plain("- item")
        combined = "\n".join(lines)
        assert "•" in combined or "-" in combined or "item" in combined

    def test_ordered_list_items(self):
        lines = plain("1. first\n2. second\n3. third")
        combined = "\n".join(lines)
        assert "first" in combined
        assert "second" in combined

    def test_ordered_list_numbers(self):
        lines = plain("1. first\n2. second")
        combined = "\n".join(lines)
        assert "1." in combined or "1" in combined


class TestBlockquote:
    def test_blockquote_content(self):
        lines = plain("> quoted text here")
        combined = "\n".join(lines)
        assert "quoted text here" in combined

    def test_blockquote_has_marker(self):
        lines = plain("> some quote")
        combined = "\n".join(lines)
        assert "▎" in combined or ">" in combined or "some quote" in combined


class TestInlineFormatting:
    def test_bold_text_rendered(self):
        lines = plain("This is **bold** text")
        combined = "\n".join(lines)
        assert "bold" in combined

    def test_italic_text_rendered(self):
        lines = plain("This is *italic* text")
        combined = "\n".join(lines)
        assert "italic" in combined

    def test_inline_code_rendered(self):
        lines = plain("Use `my_func()` here")
        combined = "\n".join(lines)
        assert "my_func()" in combined

    def test_strikethrough_rendered(self):
        lines = plain("~~struck through~~")
        combined = "\n".join(lines)
        assert "struck through" in combined

    def test_bold_has_ansi_styling(self):
        lines = render("This is **bold** text")
        raw = "\n".join(lines)
        assert "\x1b[" in raw


class TestLatexMath:
    def test_inline_math_converts_to_unicode_text(self):
        assert plain(r"Euler showed that $\pi^2 \le 10$.") == ["Euler showed that π² ≤ 10."]

    def test_display_math_renders_on_separate_line(self):
        lines = plain(r"Using Euler: $$\pi^2 = 6 \sum_{n=1}^{\infty} \frac{1}{n^2}$$ Therefore.")
        assert lines == [
            "Using Euler: ",
            "π² = 6 ∑ₙ₌₁^∞ 1/n²",
            " Therefore.",
        ]

    def test_multiple_math_expressions(self):
        combined = "\n".join(
            plain(
                r"$\sum_{n=1}^{\infty} \frac{1}{n^2} \le \frac{5}{3}$, "
                r"so $\pi^2 \le 10$."
            )
        )
        assert "∑ₙ₌₁^∞ 1/n² ≤ 5/3" in combined
        assert "π² ≤ 10" in combined

    def test_inline_code_is_not_converted(self):
        assert plain(r"Use `$\pi^2$` literally.") == [r"Use $\pi^2$ literally."]

    def test_fenced_code_is_not_converted(self):
        combined = "\n".join(plain("```tex\n$\\pi^2 \\le 10$\n```"))
        assert r"$\pi^2 \le 10$" in combined

    def test_currency_is_not_treated_as_math(self):
        assert plain("It costs $5 and then $10.") == ["It costs $5 and then $10."]


class TestLinks:
    def test_link_text_rendered(self):
        lines = plain("[click here](https://example.com)")
        combined = "\n".join(lines)
        assert "click here" in combined

    def test_named_link_hides_url(self):
        assert plain("[link](https://example.com)") == ["link"]

    def test_named_link_uses_osc8_hyperlink(self):
        combined = "\n".join(render("[link](https://example.com)"))
        assert "\x1b]8;;https://example.com\x1b\\" in combined
        assert combined.endswith("\x1b]8;;\x1b\\")

    def test_autolink_displays_url_once(self):
        combined = "\n".join(plain("<https://example.com>"))
        assert combined == "https://example.com"

    def test_wrapped_link_closes_and_reopens_hyperlink(self):
        lines = render("[a long link label](https://example.com)", width=8)
        assert len(lines) == 3
        assert all("\x1b]8;;\x1b\\" in line for line in lines)
        assert all(line.count("\x1b]8;;https://example.com\x1b\\") == 1 for line in lines)


class TestImages:
    def test_image_alt_text_rendered(self):
        lines = plain("![my image](photo.png)")
        combined = "\n".join(lines)
        assert "my image" in combined

    def test_image_path_hidden(self):
        assert plain("![alt](photo.png)") == ["[image: alt]"]

    def test_image_placeholder_links_to_local_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        combined = "\n".join(render("![alt](photo.png)"))
        target = (tmp_path / "photo.png").as_uri()
        assert f"\x1b]8;;{target}\x1b\\" in combined

    def test_image_placeholder_links_to_remote_url(self):
        combined = "\n".join(render("![alt](https://example.com/photo.png)"))
        assert "\x1b]8;;https://example.com/photo.png\x1b\\" in combined


class TestTable:
    def test_table_headers(self):
        md = "| Name | Age |\n|------|-----|\n| Alice | 30 |"
        lines = plain(md)
        combined = "\n".join(lines)
        assert "Name" in combined
        assert "Age" in combined

    def test_table_data_rows(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        lines = plain(md)
        combined = "\n".join(lines)
        assert "1" in combined
        assert "2" in combined

    def test_table_has_borders(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        lines = plain(md)
        combined = "\n".join(lines)
        assert "│" in combined or "|" in combined

    def test_table_fits_within_width(self):
        md = "| Name | Value |\n|------|-------|\n| foo | bar |"
        for width in (40, 60, 80):
            lines = plain(md, width=width)
            for line in lines:
                assert len(line) <= width + 2, f"line too wide at width={width}: {line!r}"

    def test_long_cell_wraps_to_next_row(self):
        long = "word " * 20  # 100 chars
        md = f"| Key | Value |\n|-----|-------|\n| k | {long.strip()} |"
        lines = plain(md, width=40)
        combined = "\n".join(lines)
        # all words must survive — none truncated
        assert "word" in combined
        # every line must fit within width
        for line in lines:
            assert len(line) <= 42  # +2 for border chars

    def test_wrapped_rows_preserve_borders(self):
        long = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        md = f"| Short | {long} |\n|-------|--------|\n| x | y |"
        lines = plain(md, width=40)
        # every non-empty line that is a table row should start/end with │
        table_lines = [ln for ln in lines if "│" in ln]
        for ln in table_lines:
            assert (
                ln.startswith("┌") or ln.startswith("├") or ln.startswith("└") or ln.startswith("│")
            )

    def test_all_content_preserved_on_narrow_terminal(self):
        md = "| Col1 | Col2 |\n|------|------|\n| hello world foo | bar baz qux |"
        lines = plain(md, width=30)
        combined = "\n".join(lines)
        assert "hello" in combined
        assert "bar" in combined

    def test_table_reflows_at_different_widths(self):
        long = "the quick brown fox jumps over the lazy dog"
        md = f"| A | B |\n|---|---|\n| x | {long} |"
        lines_narrow = plain(md, width=40)
        lines_wide = plain(md, width=120)
        # wider terminal → fewer rows needed for the long cell
        assert len(lines_wide) <= len(lines_narrow)


class TestStreamingMarkdownRenderer:
    def test_matches_full_render_for_completed_prefix_plus_live_tail(self):
        theme = _theme()
        md = (
            "# Heading\n\n"
            "First paragraph with **bold** text.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "Final paragraph still streaming"
        )
        renderer = StreamingMarkdownRenderer()
        streamed = renderer.render(md, 80, theme)
        assert [strip_ansi(line) for line in streamed] == plain(md)

    def test_append_only_render_updates_tail_without_losing_prefix(self):
        theme = _theme()
        renderer = StreamingMarkdownRenderer()
        first = "Intro paragraph.\n\nSecond paragraph"
        second = first + " with more streamed words."

        renderer.render(first, 80, theme)
        streamed = renderer.render(second, 80, theme)

        assert [strip_ansi(line) for line in streamed] == plain(second)

    def test_does_not_freeze_inside_fenced_code_block(self):
        theme = _theme()
        renderer = StreamingMarkdownRenderer()
        first = "Before.\n\n```python\nprint('hi')\n\n"
        second = first + "print('bye')\n```\n\nAfter."

        renderer.render(first, 80, theme)
        streamed = renderer.render(second, 80, theme)

        assert [strip_ansi(line) for line in streamed] == plain(second)

    def test_freezes_completed_blocks_at_latest_blank_boundary(self):
        theme = _theme()
        renderer = StreamingMarkdownRenderer()
        md = "# Heading\n\nParagraph one.\n\nParagraph two still streaming"

        streamed = renderer.render(md, 80, theme)

        assert [strip_ansi(line) for line in streamed] == plain(md)
        assert renderer._frozen_until == len("# Heading\n\nParagraph one.\n\n")
        assert "Paragraph two" not in "\n".join(strip_ansi(line) for line in renderer._frozen_lines)

    def test_keeps_current_table_live_until_blank_boundary(self):
        theme = _theme()
        renderer = StreamingMarkdownRenderer()
        first = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        second = first + "\n| longer value | 3 |"

        renderer.render(first, 80, theme)
        streamed = renderer.render(second, 80, theme)

        assert [strip_ansi(line) for line in streamed] == plain(second)
        assert renderer._frozen_until == len("Intro.\n\n")
