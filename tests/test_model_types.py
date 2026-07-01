"""Tests for tau/inference/model/types.py — Model cost calculations."""

from __future__ import annotations

from tau.inference.model.types import Cost, Modality, Model
from tau.message.types import Usage


def _model(**kwargs) -> Model:
    return Model(
        id=kwargs.get("id", "test-model"),
        name=kwargs.get("name", "Test Model"),
        provider=kwargs.get("provider", "test"),
        cost=kwargs.get("cost", Cost()),
        context_window=kwargs.get("context_window", 0),
        max_input_tokens=kwargs.get("max_input_tokens"),
    )


class TestModelInputLimit:
    def test_uses_context_window_when_no_max_input(self):
        m = _model(context_window=128_000)
        assert m.input_limit == 128_000

    def test_uses_max_input_tokens_when_set(self):
        m = _model(context_window=200_000, max_input_tokens=128_000)
        assert m.input_limit == 128_000

    def test_zero_context_window(self):
        m = _model(context_window=0)
        assert m.input_limit == 0


class TestModelGetters:
    def test_get_name(self):
        m = _model(name="Claude Sonnet")
        assert m.get_name() == "Claude Sonnet"

    def test_get_model_id(self):
        m = _model(id="claude-sonnet-4")
        assert m.get_model_id() == "claude-sonnet-4"

    def test_get_cost(self):
        cost = Cost(input=3.0, output=15.0)
        m = _model(cost=cost)
        assert m.get_cost() is cost


class TestModelCalculateCost:
    def test_zero_usage_zero_cost(self):
        m = _model(cost=Cost(input=3.0, output=15.0))
        usage = Usage()
        cost = m.calculate_cost(usage)
        assert cost.total == 0.0

    def test_input_cost(self):
        m = _model(cost=Cost(input=3.0))  # $3 per million
        usage = Usage(input_tokens=1_000_000)
        cost = m.calculate_cost(usage)
        assert abs(cost.input - 3.0) < 1e-9
        assert cost.total == cost.input

    def test_output_cost(self):
        m = _model(cost=Cost(output=15.0))  # $15 per million
        usage = Usage(output_tokens=1_000_000)
        cost = m.calculate_cost(usage)
        assert abs(cost.output - 15.0) < 1e-9

    def test_combined_cost(self):
        m = _model(cost=Cost(input=3.0, output=15.0))
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = m.calculate_cost(usage)
        assert abs(cost.total - 18.0) < 1e-9

    def test_cache_read_cost(self):
        m = _model(cost=Cost(cache_read=0.3))
        usage = Usage(cache_read_tokens=1_000_000)
        cost = m.calculate_cost(usage)
        assert abs(cost.cache_read - 0.3) < 1e-9

    def test_cache_write_cost(self):
        m = _model(cost=Cost(cache_write=3.75))
        usage = Usage(cache_write_tokens=1_000_000)
        cost = m.calculate_cost(usage)
        assert abs(cost.cache_write - 3.75) < 1e-9

    def test_total_includes_all(self):
        m = _model(cost=Cost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75))
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        cost = m.calculate_cost(usage)
        expected = 3.0 + 15.0 + 0.3 + 3.75
        assert abs(cost.total - expected) < 1e-9

    def test_fractional_tokens(self):
        m = _model(cost=Cost(input=3.0))
        usage = Usage(input_tokens=500_000)  # half a million
        cost = m.calculate_cost(usage)
        assert abs(cost.input - 1.5) < 1e-9


class TestModality:
    def test_all_modalities(self):
        assert Modality.Text == "text"
        assert Modality.Image == "image"
        assert Modality.Audio == "audio"
        assert Modality.Video == "video"
