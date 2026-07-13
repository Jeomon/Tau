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

from tau.inference.api.text.openai_responses import _extra_body_for
from tau.inference.model.types import Cost, Model


def _model(model_id: str, provider: str) -> Model:
    return Model(id=model_id, name=model_id, provider=provider, cost=Cost())


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
