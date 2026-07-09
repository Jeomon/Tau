"""Shared utilities for LLM API provider implementations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.tool.types import Tool

__all__ = [
    "gemini_tool_schema",
    "parse_tool_args",
    "tool_result_text",
    "openai_user_content",
    "openai_assistant_content",
    "openai_messages_to_chat",
    "openai_response_format",
    "anthropic_messages_to_list",
    "anthropic_output_config",
    "anthropic_apply_message_cache",
    "check_strict_tools_supported",
    "strict_json_schema",
    "has_tool_history",
]


def check_strict_tools_supported(tools: list[Tool] | None) -> None:
    """Raise if a tool demands strict constrained sampling from a provider that can't do it.

    Only the OpenAI Responses/Completions APIs implement wire-level strict
    function calling today, so every other provider calls this instead of
    honoring ``Tool.strict``. ``strict="require"`` must fail loudly rather than
    silently run unconstrained; ``strict="prefer"`` is a best-effort opt-in, so
    it degrades quietly on providers that can't honor it.
    """
    for tool in tools or []:
        if tool.strict == "require":
            raise ValueError(
                f"Tool '{tool.name}' requires strict constrained sampling, "
                "which this provider does not support."
            )


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively rewrite a JSON schema to satisfy OpenAI's strict tool-calling contract.

    Strict mode requires every object node to set ``additionalProperties: false``
    and list every one of its properties as required (OpenAI has no notion of an
    optional property under strict decoding).
    """
    schema = dict(schema)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {k: strict_json_schema(v) for k, v in properties.items()}
        schema["required"] = list(properties.keys())
        schema["additionalProperties"] = False
    items = schema.get("items")
    if isinstance(items, dict):
        schema["items"] = strict_json_schema(items)
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            schema[key] = [strict_json_schema(v) if isinstance(v, dict) else v for v in variants]
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        schema["$defs"] = {k: strict_json_schema(v) for k, v in defs.items()}
    return schema


_CACHE_MARKER = {"type": "ephemeral"}

_NO_TOOL_OUTPUT = "(no tool output)"

_GEMINI_UNSUPPORTED_SCHEMA_KEYS = {
    "title",
    "$schema",
    "$defs",
    "default",
    "prefixItems",
    "maxItems",
    "minItems",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "examples",
}


def gemini_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic JSON Schema to Gemini's function declaration subset."""
    defs = schema.get("$defs", {})

    def resolve(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj if not isinstance(obj, list) else [resolve(item) for item in obj]
        if "$ref" in obj:
            ref_name = obj["$ref"].rsplit("/", 1)[-1]
            return resolve(defs.get(ref_name, {}))

        result: dict[str, Any] = {}
        for key, value in obj.items():
            if key in _GEMINI_UNSUPPORTED_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # Property names are user-defined and may legitimately match an
                # unsupported schema keyword such as "title" or "default".
                result[key] = {
                    property_name: resolve(property_schema)
                    for property_name, property_schema in value.items()
                }
                continue
            if key == "anyOf" and isinstance(value, list):
                non_null = [resolve(item) for item in value if item != {"type": "null"}]
                if len(non_null) == 1:
                    result.update(non_null[0])
                else:
                    result[key] = non_null
            else:
                result[key] = resolve(value)

        if result.get("type") == "array" and "items" not in result:
            prefix = obj.get("prefixItems")
            result["items"] = (
                resolve(prefix[0]) if isinstance(prefix, list) and prefix else {"type": "string"}
            )
        return result

    return resolve(schema)


def tool_result_text(content: Any) -> str:
    """Text to send a provider for a tool result, substituting a placeholder when empty.

    Some providers reject or mishandle a bare empty string in a tool-result
    content block, so a tool that legitimately produced no output (e.g. a
    silent success) still needs non-empty text on the wire.
    """
    return content.content or _NO_TOOL_OUTPUT


def has_tool_history(messages: list[dict[str, Any]]) -> bool:
    """True if any wire-format message contains a tool_use/tool_result block.

    Anthropic rejects a request outright if such blocks exist anywhere in
    history but the top-level `tools` param is absent — an empty list must be
    sent explicitly in that case rather than omitting the key.
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def anthropic_apply_message_cache(
    messages: list[dict[str, Any]],
    n: int = 2,
    skip_tail: int = 0,
) -> list[dict[str, Any]]:
    """Inject cache_control breakpoints into the last n stable messages.

    Implements the Anthropic 'system_and_3' caching strategy — the system
    prompt is already marked by the caller; this adds up to 2 more breakpoints
    on the tail of the stable session history so the bulk of the conversation
    is served from cache on subsequent turns.

    skip_tail: number of ephemeral messages at the end of the list to skip
    (desktop/browser screenshots that change every turn and must not be cached).

    Returns a new list; the original is not mutated.
    """
    import copy

    messages = copy.deepcopy(messages)
    total = len(messages)
    stable_end = total - skip_tail  # index just past the last stable message
    stable_start = max(0, stable_end - n)
    for msg in messages[stable_start:stable_end]:
        content = msg.get("content")
        if content is None or content == "":
            msg["cache_control"] = _CACHE_MARKER
        elif isinstance(content, str):
            msg["content"] = [{"type": "text", "text": content, "cache_control": _CACHE_MARKER}]
        elif isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = _CACHE_MARKER
    return messages


def parse_tool_args(value: Any) -> dict:
    """Parse a tool-call arguments value into a dict.

    Handles the three shapes that provider APIs return:
    - already a dict  → return as-is
    - a JSON string   → parse and return (empty string → {})
    - anything else   → return {}
    Falls back to {} on JSONDecodeError.
    """
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def openai_user_content(content_items: list) -> str | list[dict[str, Any]]:
    """Convert user message contents to OpenAI chat format (completions/copilot/mistral)."""
    from tau.message.types import ImageContent, TextContent

    parts: list[dict[str, Any]] = []
    for item in content_items:
        match item:
            case TextContent():
                parts.append({"type": "text", "text": item.content})
            case ImageContent():
                for b64, mime in item.to_base64():
                    url = (
                        b64
                        if b64.startswith("http")
                        else f"data:{mime or 'image/png'};base64,{b64}"
                    )
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                if item.dimension_note:
                    parts.append({"type": "text", "text": item.dimension_note})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def openai_assistant_content(
    content_items: list,
) -> tuple[str | None, list[dict[str, Any]], str]:
    """Convert assistant message contents to OpenAI chat format (completions/copilot).

    Returns (text, tool_calls, thinking_text) — thinking_text is the concatenated
    ThinkingContent, left to the caller to re-attach per the model's dialect.
    """
    from tau.message.types import TextContent, ThinkingContent, ToolCallContent

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in content_items:
        match item:
            case TextContent():
                text_parts.append(item.content)
            case ThinkingContent():
                thinking_parts.append(item.content)
            case ToolCallContent():
                tool_calls.append(
                    {
                        "id": item.id,
                        "type": "function",
                        "function": {"name": item.name, "arguments": json.dumps(item.args)},
                    }
                )
    return "".join(text_parts) or None, tool_calls, "".join(thinking_parts)


def openai_response_format(response_format: Any | None) -> dict[str, Any] | None:
    """Convert response_format to OpenAI json_schema format (completions/copilot/mistral)."""
    from tau.inference.types import normalize_structured_response_format

    structured = normalize_structured_response_format(response_format)
    if structured is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": structured.name,
            "schema": structured.schema,
            "strict": structured.strict,
        },
    }


def openai_messages_to_chat(messages: list, model: Any = None) -> list[dict[str, Any]]:
    """Convert a message list to OpenAI chat completions format.

    ``model`` (when given) drives dialect-specific replay handling, e.g.
    re-attaching stored thinking as ``reasoning_content`` for models that
    require it on every assistant message.
    """
    from tau.inference.api.text import dialect
    from tau.message.types import (
        AssistantMessage,
        SystemMessage,
        TextContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )

    result: list[dict[str, Any]] = []
    for msg in messages:
        match msg:
            case SystemMessage():
                text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
                result.append({"role": "system", "content": text})
            case UserMessage():
                if not msg.contents:
                    continue
                result.append({"role": "user", "content": openai_user_content(msg.contents)})
            case AssistantMessage():
                text, tool_calls, thinking_text = openai_assistant_content(msg.contents)
                entry: dict[str, Any] = {"role": "assistant"}
                if text is not None:
                    entry["content"] = text
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                if model is not None:
                    dialect.attach_reasoning_for_replay(entry, model, thinking_text)
                result.append(entry)
            case ToolMessage():
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": content.id,
                                "content": tool_result_text(content),
                            }
                        )
    return result


def anthropic_messages_to_list(
    messages: list, supports_thinking: bool = True
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert a message list to Anthropic Messages API format.

    When supports_thinking is False, ThinkingContent blocks are stripped so
    non-extended-thinking models don't receive reasoning input they can't accept.
    """
    from tau.message.types import (
        AssistantMessage,
        ImageContent,
        SystemMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
    )

    system: str | None = None
    result: list[dict[str, Any]] = []
    for msg in messages:
        match msg:
            case SystemMessage():
                system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            case UserMessage():
                if not msg.contents:
                    continue
                parts: list[dict[str, Any]] = []
                has_text = False
                has_image = False
                for item in msg.contents:
                    match item:
                        case TextContent():
                            has_text = True
                            parts.append({"type": "text", "text": item.content})
                        case ImageContent():
                            has_image = True
                            for b64, mime in item.to_base64():
                                parts.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": mime or "image/png",
                                            "data": b64,
                                        },
                                    }
                                )
                            if item.dimension_note:
                                parts.append({"type": "text", "text": item.dimension_note})
                if has_image and not has_text:
                    parts.append({"type": "text", "text": "(see attached image)"})
                result.append({"role": "user", "content": parts})
            case AssistantMessage():
                parts = []
                thinking_parts: list[str] = []
                text_parts_asst: list[str] = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            if supports_thinking:
                                parts.append({"type": "text", "text": item.content})
                            else:
                                text_parts_asst.append(item.content)
                        case ThinkingContent():
                            if supports_thinking:
                                # Anthropic rejects a "thinking" block with an
                                # empty thinking field ("each thinking block
                                # must contain thinking") — drop no-op blocks
                                # (e.g. left over from a provider/model switch)
                                # instead of replaying them verbatim.
                                if item.content:
                                    parts.append(
                                        {
                                            "type": "thinking",
                                            "thinking": item.content,
                                            "signature": item.signature,
                                        }
                                    )
                            else:
                                thinking_parts.append(item.content)
                        case ToolCallContent():
                            parts.append(
                                {
                                    "type": "tool_use",
                                    "id": item.id,
                                    "name": item.name,
                                    "input": item.args,
                                }
                            )
                if not supports_thinking and (thinking_parts or text_parts_asst):
                    merged = "\n".join(thinking_parts + text_parts_asst)
                    parts.insert(0, {"type": "text", "text": merged})
                result.append({"role": "assistant", "content": parts})
            case ToolMessage():
                tool_results = []
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": content.id,
                                "content": tool_result_text(content),
                                "is_error": content.is_error,
                            }
                        )
                if tool_results:
                    result.append({"role": "user", "content": tool_results})
    return system, result


def anthropic_output_config(response_format: Any | None) -> dict[str, Any] | None:
    """Convert response_format to Anthropic output config format."""
    from tau.inference.types import normalize_structured_response_format

    structured = normalize_structured_response_format(response_format)
    if structured is None:
        return None
    return {"format": {"type": "json_schema", "schema": structured.schema}}
