"""Tests for tau/themes/loader.py — theme parsing and loading."""

from __future__ import annotations

import json

from tau.themes.loader import (
    _make_style,
    _parse_hex,
    load_theme_from_dict,
    load_theme_from_file,
    load_themes_from_dir,
    validate_theme_dict,
)
from tau.tui.style import Style, apply_style


def _minimal_theme(name: str = "test") -> dict:
    return {"name": name}


class TestParseHex:
    def test_valid_hex(self):
        assert _parse_hex("#ff8800") == (255, 136, 0)

    def test_black(self):
        assert _parse_hex("#000000") == (0, 0, 0)

    def test_white(self):
        assert _parse_hex("#ffffff") == (255, 255, 255)

    def test_uppercase_digits(self):
        assert _parse_hex("#AABBCC") == (170, 187, 204)

    def test_missing_hash_returns_none(self):
        assert _parse_hex("ff8800") is None

    def test_short_hex_returns_none(self):
        assert _parse_hex("#fff") is None

    def test_invalid_chars_returns_none(self):
        assert _parse_hex("#zzzzzz") is None

    def test_empty_string_returns_none(self):
        assert _parse_hex("") is None


class TestMakeStyle:
    def test_none_returns_none(self):
        assert _make_style(None) is None

    def test_named_color_returns_style(self):
        style = _make_style("red")
        assert isinstance(style, Style)
        assert style.fg == "red"

    def test_hex_color_returns_style(self):
        style = _make_style("#ff0000")
        assert isinstance(style, Style)
        assert style.fg == (255, 0, 0)

    def test_style_wraps_text(self):
        style = _make_style("cyan")
        result = apply_style(style, "hello")
        assert "hello" in result

    def test_dict_with_color_and_bold(self):
        style = _make_style({"color": "green", "bold": True})
        assert isinstance(style, Style)
        from tau.tui.style import Modifier

        assert Modifier.BOLD in style.add_modifier

    def test_unknown_color_name_returns_none(self):
        assert _make_style("notacolor") is None

    def test_invalid_hex_returns_none(self):
        assert _make_style("#xyz") is None


class TestLoadThemeFromDict:
    def test_missing_name_returns_error(self):
        theme, err = load_theme_from_dict({})
        assert theme is None
        assert err is not None

    def test_minimal_theme_loads(self):
        theme, err = load_theme_from_dict(_minimal_theme("dark"))
        assert err is None
        assert theme is not None

    def test_name_not_string_returns_error(self):
        theme, err = load_theme_from_dict({"name": 123})
        assert theme is None
        assert err is not None

    def test_colors_applied(self):
        data = {"name": "custom", "colors": {"heading": "#ff0000"}}
        theme, err = load_theme_from_dict(data)
        assert err is None
        assert theme is not None
        assert isinstance(theme.message.markdown.heading, Style)
        assert theme.message.markdown.heading.fg == (255, 0, 0)

    def test_show_thinking_flag(self):
        data = {"name": "t", "show_thinking": False}
        theme, _ = load_theme_from_dict(data)
        assert theme is not None
        assert theme.message.show_thinking is False

    def test_show_tool_calls_flag(self):
        data = {"name": "t", "show_tool_calls": False}
        theme, _ = load_theme_from_dict(data)
        assert theme is not None
        assert theme.message.show_tool_calls is False

    def test_spinner_custom_frames(self):
        data = {"name": "t", "spinner": {"frames": ["a", "b", "c"]}}
        theme, _ = load_theme_from_dict(data)
        assert theme is not None
        assert theme.spinner.frames == ["a", "b", "c"]


class TestValidateThemeDict:
    def test_no_warnings_for_valid_dict(self):
        assert validate_theme_dict({"name": "x", "colors": {"heading": "#ff0000"}}) == []

    def test_unknown_top_key_warns(self):
        warnings = validate_theme_dict({"name": "x", "typo_key": "value"})
        assert any("typo_key" in w for w in warnings)

    def test_unknown_color_warns(self):
        warnings = validate_theme_dict({"name": "x", "colors": {"unknown_color": "#fff"}})
        assert any("unknown_color" in w for w in warnings)

    def test_invalid_color_value_warns(self):
        warnings = validate_theme_dict({"name": "x", "colors": {"heading": "notacolor"}})
        assert any("heading" in w for w in warnings)


class TestLoadThemeFromFile:
    def test_loads_json_theme(self, tmp_path):
        f = tmp_path / "dark.json"
        f.write_text(json.dumps({"name": "dark"}))
        theme, err = load_theme_from_file(f)
        assert err is None
        assert theme is not None

    def test_loads_yaml_theme(self, tmp_path):
        f = tmp_path / "light.yaml"
        f.write_text("name: light\n")
        theme, err = load_theme_from_file(f)
        assert err is None
        assert theme is not None

    def test_invalid_json_returns_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{bad json}")
        theme, err = load_theme_from_file(f)
        assert theme is None
        assert err is not None

    def test_nonexistent_file_returns_error(self, tmp_path):
        theme, err = load_theme_from_file(tmp_path / "ghost.json")
        assert theme is None
        assert err is not None


class TestLoadThemesFromDir:
    def test_empty_dir_returns_empty(self, tmp_path):
        result = load_themes_from_dir(tmp_path)
        assert result.themes == {}

    def test_missing_dir_returns_empty(self, tmp_path):
        result = load_themes_from_dir(tmp_path / "nope")
        assert result.themes == {}

    def test_loads_all_json_yaml(self, tmp_path):
        (tmp_path / "dark.json").write_text(json.dumps({"name": "dark"}))
        (tmp_path / "light.yaml").write_text("name: light\n")
        result = load_themes_from_dir(tmp_path)
        assert "dark" in result.themes
        assert "light" in result.themes

    def test_ignores_non_theme_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("ignore")
        (tmp_path / "dark.json").write_text(json.dumps({"name": "dark"}))
        result = load_themes_from_dir(tmp_path)
        assert len(result.themes) == 1

    def test_error_collected_for_missing_name(self, tmp_path):
        (tmp_path / "bad.json").write_text(json.dumps({"colors": {}}))
        result = load_themes_from_dir(tmp_path)
        assert len(result.errors) >= 1

    def test_names_are_lowercased(self, tmp_path):
        (tmp_path / "t.json").write_text(json.dumps({"name": "MyTheme"}))
        result = load_themes_from_dir(tmp_path)
        assert "mytheme" in result.themes
