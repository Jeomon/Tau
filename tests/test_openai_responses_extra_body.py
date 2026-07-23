"""Regression tests for the extra_body gating in the direct OpenAI Responses adapter.

api="openai_responses" is shared by four providers (openai, perplexity, xai,
bedrock — see tau/builtins/providers/text.py). bedrock in particular proxies
real OpenAI model ids (e.g. "openai.gpt-5.5") through a different backend (AWS's
Mantle gateway) than OpenAI's own servers. A gate keyed only on model id, not
provider, would leak an OpenAI-only field like prompt_cache_options to that
proxy the moment a "openai.gpt-5.6*" model is added to the bedrock catalog —
the same class of bug already hit (and fixed) on the Codex OAuth path, where
the ChatGPT/Codex backend rejected prompt_cache_options with HTTP 400
"Unsupported parameter".
"""

from __future__ import annotations

from tau.inference.api.text.openai_responses import _cache_body_for, _extra_body_for
from tau.inference.model.types import Cost, Model


def _model(model_id: str, provider: str, supports_long: bool = True) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        provider=provider,
        cost=Cost(),
        supports_long_cache_retention=supports_long,
    )


def test_gpt56_on_openai_gets_prompt_cache_options() -> None:
    extra_body = _extra_body_for(_model("gpt-5.6-sol", "openai"))
    assert extra_body == {"prompt_cache_options": {"mode": "implicit", "ttl": "30m"}}


def test_gpt55_on_openai_gets_nothing() -> None:
    assert _extra_body_for(_model("gpt-5.5", "openai")) == {}


def test_gpt56_shaped_id_on_bedrock_gets_nothing() -> None:
    """A future "openai.gpt-5.6" bedrock model (following the existing
    "openai.gpt-5.5" naming convention) must not inherit the OpenAI-only field.
    """
    assert _extra_body_for(_model("openai.gpt-5.6", "bedrock")) == {}
    assert _extra_body_for(_model("openai.gpt-5.6-sol", "bedrock")) == {}


def test_gpt56_shaped_id_on_xai_or_perplexity_gets_nothing() -> None:
    assert _extra_body_for(_model("gpt-5.6-proxy", "xai")) == {}
    assert _extra_body_for(_model("gpt-5.6-proxy", "perplexity")) == {}


class TestCacheBodyFor:
    def test_short_sends_only_cache_key(self) -> None:
        body = _cache_body_for(_model("gpt-5.5", "openai"), "short", "sess123")
        assert body == {"prompt_cache_key": "sess123"}

    def test_long_adds_24h_retention(self) -> None:
        body = _cache_body_for(_model("gpt-5.5", "openai"), "long", "sess123")
        assert body == {"prompt_cache_key": "sess123", "prompt_cache_retention": "24h"}

    def test_long_without_model_support_omits_retention(self) -> None:
        body = _cache_body_for(_model("gpt-5.5", "openai", supports_long=False), "long", "sess123")
        assert body == {"prompt_cache_key": "sess123"}

    def test_none_drops_cache_key_and_retention(self) -> None:
        assert _cache_body_for(_model("gpt-5.5", "openai"), "none", "sess123") == {}

    def test_non_openai_provider_gets_nothing(self) -> None:
        """Proxies (perplexity/xai/bedrock) must not receive OpenAI-only fields."""
        assert _cache_body_for(_model("gpt-5.6-proxy", "xai"), "long", "sess123") == {}
        assert _cache_body_for(_model("openai.gpt-5.6", "bedrock"), "long", "sess123") == {}
