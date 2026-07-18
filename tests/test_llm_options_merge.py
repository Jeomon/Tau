"""Regression tests for TextLLM._merge_options and LLMOptions explicit-field
tracking.

LLMOptions has non-None dataclass defaults (temperature=1.0, max_retries=3,
retry_base_delay_ms=1000, timeout=60s, distrust_thought_signatures=False), so a
merge that only skips None override fields silently clobbered a provider's base
options whenever any options object was passed. Only explicitly-set override
fields may win.
"""

from __future__ import annotations

from datetime import timedelta

from tau.inference.api.text.service import TextLLM
from tau.inference.types import LLMOptions, Transport


def _merge(base: LLMOptions, override: LLMOptions | None) -> LLMOptions:
    return TextLLM._merge_options(object.__new__(TextLLM), base, override)


def test_constructed_options_expose_real_defaults() -> None:
    opts = LLMOptions()
    assert opts.temperature == 1.0
    assert opts.max_retries == 3
    assert opts.retry_base_delay_ms == 1000
    assert opts.timeout == timedelta(seconds=60)
    assert opts.transport == Transport.HTTP
    assert opts.distrust_thought_signatures is False


def test_defaulted_override_fields_do_not_clobber_base() -> None:
    base = LLMOptions(
        temperature=0.2,
        max_retries=5,
        retry_base_delay_ms=250,
        timeout=timedelta(seconds=300),
    )
    merged = _merge(base, LLMOptions(api_key="k"))
    assert merged.api_key == "k"
    assert merged.temperature == 0.2
    assert merged.max_retries == 5
    assert merged.retry_base_delay_ms == 250
    assert merged.timeout == timedelta(seconds=300)


def test_explicit_value_equal_to_default_still_overrides() -> None:
    base = LLMOptions(temperature=0.2)
    merged = _merge(base, LLMOptions(temperature=1.0))
    assert merged.temperature == 1.0


def test_explicit_non_default_value_overrides() -> None:
    base = LLMOptions(temperature=0.2, max_retries=5)
    merged = _merge(base, LLMOptions(temperature=0.7, max_retries=0))
    assert merged.temperature == 0.7
    assert merged.max_retries == 0


def test_post_construction_assignment_counts_as_explicit() -> None:
    base = LLMOptions(max_retries=5)
    override = LLMOptions()
    override.max_retries = 3
    merged = _merge(base, override)
    assert merged.max_retries == 3


def test_none_override_returns_base() -> None:
    base = LLMOptions(temperature=0.2)
    assert _merge(base, None) is base
