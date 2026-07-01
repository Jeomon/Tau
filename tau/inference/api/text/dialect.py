"""Per-model "thinking dialect" handling for OpenAI-compatible chat completions.

Providers that don't natively separate reasoning from text the way Anthropic
(structured thinking blocks) or Mistral (chunked content) do instead stream it
as a sibling delta field (``reasoning_content``, ``reasoning``, ...) alongside
plain ``content``, and each asks for/expects that reasoning back differently.
``Model.thinking_format`` names which of these dialects a model speaks; ``None``
means the generic OpenAI ``reasoning_effort`` request shape with no special
replay handling.
"""

from __future__ import annotations

from typing import Any

from tau.inference.model.types import Model
from tau.inference.types import LLMOptions, ThinkingLevel

# Dialects observed across OpenAI-compatible providers. Only "deepseek" is
# currently assigned to a Tau model; the others are wired up so a model can
# opt in the moment Tau adds provider coverage for it.
ZAI = "zai"
QWEN = "qwen"
QWEN_CHAT_TEMPLATE = "qwen-chat-template"
CHAT_TEMPLATE = "chat-template"
DEEPSEEK = "deepseek"
OPENROUTER = "openrouter"
ANT_LING = "ant-ling"
TOGETHER = "together"
STRING_THINKING = "string-thinking"

# Dialects whose assistant messages must carry the reasoning field back
# on replay (the provider 400s otherwise), and the field name to use.
_REPLAY_FIELD: dict[str, str] = {
    DEEPSEEK: "reasoning_content",
}

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
    """Return request params to merge in to enable/configure reasoning for this model.

    Dispatches on ``model.thinking_format``; models with no tag (or a tag Tau
    doesn't have provider coverage for yet) fall through to the plain
    ``reasoning_effort`` shape.
    """
    if not model.thinking:
        return {}

    effort = _effort(options)
    tag = model.thinking_format

    if tag == ZAI:
        params: dict[str, Any] = {"thinking": {"type": "enabled" if effort else "disabled"}}
        if effort:
            params["reasoning_effort"] = effort
        return params

    if tag == QWEN:
        return {"enable_thinking": bool(effort)}

    if tag == QWEN_CHAT_TEMPLATE:
        return {"chat_template_kwargs": {"enable_thinking": bool(effort), "preserve_thinking": True}}

    if tag == CHAT_TEMPLATE:
        return {"chat_template_kwargs": {"enable_thinking": bool(effort)}}

    if tag == DEEPSEEK:
        params = {"thinking": {"type": "enabled" if effort else "disabled"}}
        if effort:
            params["reasoning_effort"] = effort
        return params

    if tag == OPENROUTER:
        # OpenRouter normalizes reasoning across providers via a nested object.
        return {"reasoning": {"effort": effort or "none"}}

    if tag == ANT_LING:
        return {"reasoning": {"effort": effort}} if effort else {}

    if tag == TOGETHER:
        params = {"reasoning": {"enabled": bool(effort)}}
        if effort:
            params["reasoning_effort"] = effort
        return params

    if tag == STRING_THINKING:
        return {"thinking": effort or "none"}

    if effort:
        return {"reasoning_effort": effort}
    return {}


def extract_thinking_delta(delta: Any) -> str | None:
    """Pull the reasoning text out of a streamed delta, if present.

    Field naming isn't dialect-specific on the response side — providers use
    one of a handful of field names regardless of ``thinking_format`` — so this
    checks all of them once rather than dispatching per model.
    """
    for name in _THINKING_DELTA_FIELDS:
        value = getattr(delta, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def attach_reasoning_for_replay(entry: dict[str, Any], model: Model, thinking_text: str) -> None:
    """Re-attach previously-streamed reasoning onto a replayed assistant message.

    No-op for models whose dialect doesn't require it (``thinking_format`` unset
    or not in ``_REPLAY_FIELD``).
    """
    field = _REPLAY_FIELD.get(model.thinking_format or "")
    if field is not None:
        entry[field] = thinking_text
