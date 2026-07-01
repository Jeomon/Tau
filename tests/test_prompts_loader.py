"""Tests for tau/prompts/loader.py — prompt template loading."""

from __future__ import annotations

from tau.prompts.loader import _parse_frontmatter, load_template_from_file, load_templates_from_dir


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just content here.")
        assert meta == {}
        assert body == "Just content here."

    def test_frontmatter_parsed(self):
        text = "---\ndescription: My template\nargument-hint: <name>\n---\nBody text."
        meta, body = _parse_frontmatter(text)
        assert meta["description"] == "My template"
        assert meta["argument-hint"] == "<name>"
        assert body == "Body text."

    def test_empty_frontmatter(self):
        text = "---\n---\nBody."
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == "Body."

    def test_unclosed_frontmatter_ignored(self):
        text = "---\ndescription: test\nBody without closing."
        meta, body = _parse_frontmatter(text)
        assert meta == {}

    def test_leading_newlines_stripped(self):
        text = "\n\n---\ndescription: test\n---\nBody."
        meta, body = _parse_frontmatter(text)
        assert meta["description"] == "test"
        assert body == "Body."

    def test_colon_in_value(self):
        text = "---\nurl: http://example.com\n---\nBody."
        meta, body = _parse_frontmatter(text)
        assert meta["url"] == "http://example.com"


class TestLoadTemplateFromFile:
    def test_loads_template(self, tmp_path):
        f = tmp_path / "greet.md"
        f.write_text("---\ndescription: Greeting template\n---\nHello, {{name}}!")
        tmpl, err = load_template_from_file(f)
        assert err is None
        assert tmpl is not None
        assert tmpl.name == "greet"
        assert tmpl.description == "Greeting template"
        assert "Hello" in tmpl.content

    def test_description_from_body_when_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("# My Template\n\nBody here.")
        tmpl, err = load_template_from_file(f)
        assert err is None
        assert tmpl is not None
        assert "My Template" in tmpl.description

    def test_empty_body_returns_error(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("---\ndescription: Empty\n---\n   ")
        tmpl, err = load_template_from_file(f)
        assert tmpl is None
        assert err is not None

    def test_argument_hint_loaded(self, tmp_path):
        f = tmp_path / "hint.md"
        f.write_text("---\nargument-hint: <name>\n---\nTemplate body.")
        tmpl, err = load_template_from_file(f)
        assert err is None
        assert tmpl is not None
        assert tmpl.argument_hint == "<name>"

    def test_file_path_stored(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("Body content.")
        tmpl, _ = load_template_from_file(f)
        assert tmpl is not None
        assert tmpl.file_path == str(f)

    def test_nonexistent_file_returns_error(self, tmp_path):
        tmpl, err = load_template_from_file(tmp_path / "nope.md")
        assert tmpl is None
        assert err is not None

    def test_name_is_lowercase_stem(self, tmp_path):
        f = tmp_path / "MyTemplate.md"
        f.write_text("Body here.")
        tmpl, _ = load_template_from_file(f)
        assert tmpl is not None
        assert tmpl.name == "mytemplate"


class TestLoadTemplatesFromDir:
    def test_empty_dir(self, tmp_path):
        result = load_templates_from_dir(tmp_path)
        assert result.templates == {}
        assert result.errors == []

    def test_nonexistent_dir(self, tmp_path):
        result = load_templates_from_dir(tmp_path / "missing")
        assert result.templates == {}

    def test_loads_all_md_files(self, tmp_path):
        (tmp_path / "a.md").write_text("Body A.")
        (tmp_path / "b.md").write_text("Body B.")
        result = load_templates_from_dir(tmp_path)
        assert "a" in result.templates
        assert "b" in result.templates

    def test_non_md_files_ignored(self, tmp_path):
        (tmp_path / "note.txt").write_text("text file")
        (tmp_path / "tmpl.md").write_text("Body.")
        result = load_templates_from_dir(tmp_path)
        assert "note" not in result.templates
        assert "tmpl" in result.templates

    def test_errors_collected(self, tmp_path):
        (tmp_path / "bad.md").write_text("---\ndescription: bad\n---\n   ")
        (tmp_path / "good.md").write_text("Body.")
        result = load_templates_from_dir(tmp_path)
        assert len(result.errors) == 1
        assert "good" in result.templates

    def test_templates_sorted_by_name(self, tmp_path):
        (tmp_path / "z.md").write_text("Z body.")
        (tmp_path / "a.md").write_text("A body.")
        result = load_templates_from_dir(tmp_path)
        keys = list(result.templates.keys())
        assert keys == sorted(keys)
