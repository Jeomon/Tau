"""Tests for the built-in footer model badge."""

from __future__ import annotations

from dataclasses import dataclass

from tau.builtins.extensions.footer.model import ModelBadge


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens_include_cache_read: bool = False


@dataclass
class _Response:
    usage: _Usage


class _Context:
    def __init__(self, tokens: int = 100, context_window: int = 1_000) -> None:
        self.tokens = tokens
        self.context_window = context_window

    def get_context_usage(self) -> dict[str, int]:
        return {
            "tokens": self.tokens,
            "context_window": self.context_window,
        }


def test_response_usage_updates_badge_immediately() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(_Usage(input_tokens=200, output_tokens=50))

    badge.update_context_from_response(response, _Context())

    assert "25%" in badge.render(80)[0]


def test_response_usage_includes_cache_tokens() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(
        _Usage(
            input_tokens=100,
            output_tokens=25,
            cache_read_tokens=300,
            cache_write_tokens=75,
        )
    )

    badge.update_context_from_response(response, _Context(context_window=1_000))

    assert "50%" in badge.render(80)[0]


def test_response_usage_does_not_double_count_inclusive_cache_tokens() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")
    response = _Response(
        _Usage(
            input_tokens=500,
            output_tokens=100,
            cache_read_tokens=400,
            input_tokens_include_cache_read=True,
        )
    )

    badge.update_context_from_response(response, _Context(context_window=1_000))

    assert "60%" in badge.render(80)[0]


def test_missing_response_usage_falls_back_to_context() -> None:
    badge = ModelBadge()
    badge.set_model("test-model", "test-provider")

    badge.update_context_from_response(object(), _Context(tokens=400, context_window=1_000))

    assert "40%" in badge.render(80)[0]
