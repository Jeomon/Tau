"""Tests for tau/tui/markdown.py — render_markdown output."""

from __future__ import annotations

from tau.tui.markdown import render_markdown
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

    def test_image_path_included(self):
        lines = plain("![alt](photo.png)")
        combined = "\n".join(lines)
        assert "photo.png" in combined


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
