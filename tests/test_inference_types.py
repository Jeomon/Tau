"""Tests for tau/inference/types.py — shared inference type logic."""

from __future__ import annotations

from tau.inference.types import (
    StructuredResponseFormat,
    ThinkingBudgets,
    ThinkingLevel,
    normalize_structured_response_format,
)


class TestThinkingBudgets:
    def test_get_minimal(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.Minimal) == 1024

    def test_get_low(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.Low) == 2048

    def test_get_medium(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.Medium) == 4096

    def test_get_high(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.High) == 8192

    def test_get_xhigh(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.XHigh) == 16384

    def test_get_max(self):
        b = ThinkingBudgets()
        assert b.get(ThinkingLevel.Max) == 32768

    def test_custom_budget_used(self):
        b = ThinkingBudgets(minimal=512)
        assert b.get(ThinkingLevel.Minimal) == 512

    def test_none_field_falls_back_to_default(self):
        b = ThinkingBudgets(minimal=None)
        assert b.get(ThinkingLevel.Minimal) == 1024


class TestNormalizeStructuredResponseFormat:
    def test_none_returns_none(self):
        assert normalize_structured_response_format(None) is None

    def test_passthrough_structured_format(self):
        f = StructuredResponseFormat(schema={"type": "object"}, name="MySchema")
        result = normalize_structured_response_format(f)
        assert result is f

    def test_dict_becomes_structured(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        result = normalize_structured_response_format(schema)
        assert result is not None
        assert result.schema == schema

    def test_pydantic_model_class(self):
        from pydantic import BaseModel

        class MyModel(BaseModel):
            name: str
            value: int

        result = normalize_structured_response_format(MyModel)
        assert result is not None
        assert result.name == "MyModel"
        assert "properties" in result.schema or "type" in result.schema

    def test_dict_default_name(self):
        result = normalize_structured_response_format({"type": "string"})
        assert result is not None
        assert result.name == "response"

    def test_dict_default_strict(self):
        result = normalize_structured_response_format({"type": "string"})
        assert result is not None
        assert result.strict is True
