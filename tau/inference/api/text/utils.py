"""Shared utilities for LLM API provider implementations."""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "gemini_tool_schema",
    "parse_tool_args",
    "tool_result_text",
    "anthropic_tool_result_content",
    "gemini_function_response_parts",
    "gemini_function_response_parts_raw",
    "openai_responses_function_call_output",
    "openai_user_content",
    "openai_assistant_content",
    "openai_messages_to_chat",
    "openai_response_format",
    "openai_gpt56_prompt_cache_options",
    "anthropic_messages_to_list",
    "anthropic_output_config",
    "anthropic_apply_message_cache",
    "has_tool_history",
]


_CACHE_MARKER = {"type": "ephemeral"}

_NO_TOOL_OUTPUT = "(no tool output)"

# GPT-5.6 adds an explicit prompt_cache_options request field (mode/ttl) and a
# matching cache_write_tokens usage field, on both the direct Responses API
# and the Codex/ChatGPT OAuth backend (which speaks the same Responses shape).
# Older models keep the old implicit (no-config) caching behavior, so this is
# only sent for the 5.6 family. See https://github.com/anomalyco/opencode/pull/36320.
_GPT56_RE = re.compile(r"(?:^|[/.])gpt-5\.6(?:$|[-_/.])", re.I)
_GPT56_PROMPT_CACHE_OPTIONS = {"mode": "implicit", "ttl": "30m"}


def openai_gpt56_prompt_cache_options(model_id: str) -> dict[str, str] | None:
    """Return the request-level prompt_cache_options for GPT-5.6+ models, else None."""
    return dict(_GPT56_PROMPT_CACHE_OPTIONS) if _GPT56_RE.search(model_id) else None


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


def anthropic_tool_result_content(content: Any) -> str | list[dict[str, Any]]:
    """Anthropic tool_result 'content': plain text, or a [text, image] block list.

    Anthropic's tool_result natively accepts a content array mixing text and
    image blocks (unlike most other providers' tool/function-result shapes,
    which are text-only) — see Handle tool calls in the Anthropic docs.
    """
    if content.image is None:
        return tool_result_text(content)
    blocks: list[dict[str, Any]] = [{"type": "text", "text": tool_result_text(content)}]
    for b64, mime in content.image.to_base64():
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime or "image/png", "data": b64},
            }
        )
    return blocks


def gemini_function_response_parts(content: Any) -> list[Any] | None:
    """FunctionResponse.parts for a tool result's attached image, or None.

    Gemini's FunctionResponse has a dedicated ``parts`` field (separate from
    the JSON-only ``response`` dict) specifically for multimodal function
    results — for the google-genai SDK classes, shared by gemini_generate.py
    and google_vertex.py (both build genai_types.Content directly, unlike
    google_antigravity.py's raw REST dicts).
    """
    if content.image is None:
        return None
    from google.genai import types as genai_types

    return [
        genai_types.FunctionResponsePart(
            inline_data=genai_types.FunctionResponseBlob(mime_type=mime or "image/png", data=b64)
        )
        for b64, mime in content.image.to_base64()
    ]


def openai_responses_function_call_output(content: Any) -> str | list[dict[str, Any]]:
    """function_call_output 'output' for the Responses/Codex-Responses APIs.

    ``output`` accepts a plain string or a list of input_text/input_image/
    input_file items — the OpenAI SDK's ResponseFunctionCallOutputItemParam
    union (same input_image shape used for regular input content).
    """
    if content.image is None:
        return tool_result_text(content)
    blocks: list[dict[str, Any]] = [{"type": "input_text", "text": tool_result_text(content)}]
    for b64, mime in content.image.to_base64():
        url = b64 if b64.startswith("http") else f"data:{mime or 'image/png'};base64,{b64}"
        blocks.append({"type": "input_image", "image_url": url})
    return blocks


def gemini_function_response_parts_raw(content: Any) -> list[dict[str, Any]] | None:
    """Like gemini_function_response_parts, but REST-JSON-shaped for google_antigravity.py,
    which builds raw dicts instead of google-genai SDK objects.
    """
    if content.image is None:
        return None
    return [
        {"inlineData": {"mimeType": mime or "image/png", "data": b64}}
        for b64, mime in content.image.to_base64()
    ]


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
    """Convert user message contents to OpenAI chat format (completions/copilot/mistral).

    Shared by every "openai_completions"-family provider (OpenAI Completions,
    GitHub Copilot, OpenAI Vertex, OpenRouter) plus Mistral. AudioContent is
    only reachable here for models a curator explicitly flagged with
    Modality.Audio — as of this writing that's a subset of OpenRouter's
    proxied models (real audio-capable backends like Gemini-via-OpenRouter),
    not the OpenAI/Copilot/Vertex/Mistral models themselves, none of which
    currently claim Modality.Audio.
    """
    from tau.message.types import AudioContent, ImageContent, TextContent

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
            case AudioContent():
                for b64, mime in item.to_base64():
                    # OpenAI's input_audio only accepts "wav" or "mp3" — map what
                    # we can detect to those two; anything else defaults to mp3
                    # (the more common wire format) rather than dropping it.
                    fmt = "wav" if mime == "audio/wav" else "mp3"
                    parts.append(
                        {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
                    )
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
        FileContent,
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
                has_file = False
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
                        case FileContent():
                            has_file = True
                            for b64, mime in item.to_base64():
                                parts.append(
                                    {
                                        "type": "document",
                                        "source": {
                                            "type": "base64",
                                            "media_type": mime,
                                            "data": b64,
                                        },
                                    }
                                )
                if (has_image or has_file) and not has_text:
                    label = "image" if has_image and not has_file else "file"
                    parts.append({"type": "text", "text": f"(see attached {label})"})
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
                                # instead of replaying them verbatim. But a
                                # signed block must survive even with empty
                                # text (some models redact the visible
                                # reasoning while still returning a valid
                                # signature) — dropping it discards the
                                # signature Anthropic needs to replay the turn.
                                if item.content or item.signature:
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
                                "content": anthropic_tool_result_content(content),
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
