"""Per-model reasoning-effort handling for OpenAI-compatible chat completions.

Providers that don't natively separate reasoning from text the way Anthropic
(structured thinking blocks) or Mistral (chunked content) do instead stream it
as a sibling delta field (``reasoning_content``, ``reasoning``, ...) alongside
plain ``content``. This module maps a model's requested thinking level to the
generic OpenAI ``reasoning_effort`` request shape.
"""

from __future__ import annotations

from typing import Any

from tau.inference.model.types import Model
from tau.inference.types import LLMOptions, ThinkingLevel

_REASONING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Minimal: "low",
    ThinkingLevel.Low: "low",
    ThinkingLevel.Medium: "medium",
    ThinkingLevel.High: "high",
    ThinkingLevel.XHigh: "high",
    ThinkingLevel.Max: "high",
}

# Fields tried in order when pulling reasoning out of a streamed delta —
# providers disagree on the name but never send more than one of these.
_THINKING_DELTA_FIELDS = ("reasoning_content", "reasoning", "reasoning_text", "thinking")


def _effort(options: LLMOptions) -> str | None:
    """Return the mapped reasoning-effort string, or None if thinking is off."""
    level = options.thinking_level
    if level is None or level == ThinkingLevel.Off:
        return None
    return _REASONING_EFFORT.get(level)


def build_reasoning_request_params(model: Model, options: LLMOptions) -> dict[str, Any]:
    """Return request params to merge in to enable/configure reasoning for this model."""
    if not model.thinking:
        return {}

    effort = _effort(options)
    if effort:
        return {"reasoning_effort": effort}
    return {}


def extract_thinking_delta(delta: Any) -> str | None:
    """Pull the reasoning text out of a streamed delta, if present.

    Field naming isn't dialect-specific on the response side — providers use
    one of a handful of field names — so this checks all of them once rather
    than dispatching per model.
    """
    for name in _THINKING_DELTA_FIELDS:
        value = getattr(delta, name, None)
        if isinstance(value, str) and value:
            return value
    return None
