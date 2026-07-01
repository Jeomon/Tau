"""Tests for tau/settings/utils.py — nested dict setter and enum coercion."""

from __future__ import annotations

from enum import Enum

from tau.settings.utils import coerce_enum, set_nested


class Color(Enum):
    RED = "red"
    GREEN = "green"


class TestSetNested:
    def test_top_level_key(self):
        d: dict = {}
        set_nested(d, "key", "value")
        assert d == {"key": "value"}

    def test_nested_key(self):
        d: dict = {}
        set_nested(d, "a.b", "value")
        assert d == {"a": {"b": "value"}}

    def test_deeply_nested_key(self):
        d: dict = {}
        set_nested(d, "a.b.c", 42)
        assert d["a"]["b"]["c"] == 42

    def test_overwrites_existing_value(self):
        d = {"key": "old"}
        set_nested(d, "key", "new")
        assert d["key"] == "new"

    def test_creates_intermediate_dict(self):
        d: dict = {"a": "not-a-dict"}
        set_nested(d, "a.b", "value")
        assert d["a"] == {"b": "value"}

    def test_preserves_sibling_keys(self):
        d = {"a": {"x": 1}}
        set_nested(d, "a.y", 2)
        assert d["a"]["x"] == 1
        assert d["a"]["y"] == 2

    def test_none_value(self):
        d: dict = {}
        set_nested(d, "key", None)
        assert d["key"] is None

    def test_list_value(self):
        d: dict = {}
        set_nested(d, "items", [1, 2, 3])
        assert d["items"] == [1, 2, 3]


class TestCoerceEnum:
    def test_none_returns_none(self):
        assert coerce_enum(Color, None) is None

    def test_already_enum_passthrough(self):
        result = coerce_enum(Color, Color.RED)
        assert result is Color.RED

    def test_string_coerced_to_enum(self):
        result = coerce_enum(Color, "red")
        assert result == Color.RED

    def test_invalid_value_returns_none(self):
        result = coerce_enum(Color, "purple")
        assert result is None

    def test_wrong_type_returns_none(self):
        result = coerce_enum(Color, 42)
        assert result is None
