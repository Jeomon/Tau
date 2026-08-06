"""Shared utilities for LLM API provider implementations."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncGenerator, Callable
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
    "anthropic_thinking_params",
    "anthropic_apply_message_cache",
    "resolve_cache_retention",
    "anthropic_cache_control",
    "openai_prompt_cache_retention",
    "has_tool_history",
    "drop_orphan_function_call_outputs",
]


_CACHE_MARKER = {"type": "ephemeral"}

#: Content-block types Anthropic accepts a ``cache_control`` breakpoint on.
#: Everything else — notably ``thinking``/``redacted_thinking`` — is rejected
#: outright: ``messages.N.content.0.thinking.cache_control: Extra inputs are not
#: permitted`` 400s the whole request and kills the turn.
#:
#: Deliberately an allowlist. The failure modes are asymmetric: an unknown type
#: we skip costs one breakpoint (marginally more expensive), while an unknown
#: type we mark costs the entire turn. A denylist gets that trade backwards.
_CACHEABLE_BLOCK_TYPES = frozenset({"text", "image", "tool_use", "tool_result", "document"})

_VALID_CACHE_RETENTIONS = ("none", "short", "long")


def resolve_cache_retention(retention: str | None = None) -> str:
    """Resolve the effective Anthropic cache-retention preference.

    Precedence: an explicit value (one of "none"/"short"/"long"), else the
    TAU_CACHE_RETENTION env var, else "short" (Anthropic's default 5-minute
    TTL). Unrecognised values fall through to "short" rather than raising, so a
    typo degrades to the safe default instead of breaking inference.
    """
    for candidate in (retention, os.environ.get("TAU_CACHE_RETENTION")):
        if candidate and candidate.strip().lower() in _VALID_CACHE_RETENTIONS:
            return candidate.strip().lower()
    return "short"


def anthropic_cache_control(
    supports_long_retention: bool, retention: str | None = None
) -> dict[str, Any] | None:
    """Build the Anthropic cache_control marker for the resolved retention.

    Returns None when retention resolves to "none" (caching disabled — callers
    should omit cache_control entirely). The "1h" TTL is only requested when
    retention is "long" AND the model advertises support; otherwise the marker
    omits ttl and Anthropic applies its default 5-minute retention. The 1-hour
    TTL is generally available and needs no beta header.
    """
    resolved = resolve_cache_retention(retention)
    if resolved == "none":
        return None
    if resolved == "long" and supports_long_retention:
        return {"type": "ephemeral", "ttl": "1h"}
    return dict(_CACHE_MARKER)


def openai_prompt_cache_retention(
    supports_long_retention: bool, retention: str | None = None
) -> str | None:
    """Return the OpenAI Responses `prompt_cache_retention` value, or None.

    OpenAI's extended prompt-cache TTL is "24h" (vs. Anthropic's "1h"), requested
    only when retention resolves to "long" AND the model advertises support.
    "short"/"none" leave the field unset so OpenAI applies its implicit default.
    """
    resolved = resolve_cache_retention(retention)
    return "24h" if resolved == "long" and supports_long_retention else None


_NO_TOOL_OUTPUT = "(no tool output)"

# GPT-5.6 adds an explicit prompt_cache_options request field (mode/ttl) and a
# matching cache_write_tokens usage field, on the direct Responses API
# (openai_responses.py). The Codex/ChatGPT OAuth backend rejects this field
# with HTTP 400 "Unsupported parameter" despite speaking the same Responses
# shape otherwise, so openai_codex_responses.py never calls this helper.
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
        url = image_data_url(b64, mime)
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


def drop_orphan_function_call_outputs(
    input_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop ``function_call_output`` items whose ``call_id`` has no matching ``function_call``.

    The OpenAI Responses/Codex API rejects the whole request with
    ``400 No tool call found for function call output with call_id ...`` if a tool
    result is present without its originating call. That can happen when a
    compaction boundary (or an extension-supplied boundary) folds a tool call into
    the summary while keeping its result, leaving an orphaned result in the
    reconstructed context — permanently wedging the session. Filtering the orphan
    here is a defensive backstop so a single stray result can't brick a session.

    Only orphaned *outputs* are dropped, not orphaned *calls*: in the Responses API
    a reasoning item must immediately precede the ``function_call`` it justified, so
    removing a call would strand its reasoning item and create a different
    malformation. Orphaned outputs are the shape that actually triggers the 400.
    """
    present_calls = {
        item.get("call_id") for item in input_items if item.get("type") == "function_call"
    }
    return [
        item
        for item in input_items
        if not (
            item.get("type") == "function_call_output" and item.get("call_id") not in present_calls
        )
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
    marker: dict[str, Any] | None = _CACHE_MARKER,
) -> list[dict[str, Any]]:
    """Inject cache_control breakpoints into the last n stable messages.

    Implements the Anthropic 'system_and_3' caching strategy — the system
    prompt is already marked by the caller; this adds up to 2 more breakpoints
    on the tail of the stable session history so the bulk of the conversation
    is served from cache on subsequent turns.

    skip_tail: number of ephemeral messages at the end of the list to skip
    (desktop/browser screenshots that change every turn and must not be cached).

    marker: the cache_control value to inject (e.g. a "1h" TTL marker for long
    retention). Defaults to the 5-minute ephemeral marker. Pass None to disable
    caching entirely ("none" retention) — the history is returned with no
    breakpoints.

    Returns a new list; the original is not mutated.
    """
    import copy

    messages = copy.deepcopy(messages)
    if marker is None:
        return messages
    total = len(messages)
    stable_end = total - skip_tail  # index just past the last stable message
    stable_start = max(0, stable_end - n)
    for msg in messages[stable_start:stable_end]:
        content = msg.get("content")
        if content is None or content == "":
            # Nothing to cache, and `cache_control` is only accepted on content
            # blocks, system blocks and tool definitions — never on the message
            # object itself. Marking one here would be the same mistake as
            # marking a thinking block, one level up. Skip it.
            continue
        if isinstance(content, str):
            msg["content"] = [{"type": "text", "text": content, "cache_control": marker}]
        elif isinstance(content, list) and content:
            # Walk back to the last block that can actually carry a breakpoint.
            # An assistant turn can end with — or consist entirely of — thinking
            # blocks, and stamping one fails the *whole* request with
            # "messages.N.content.0.thinking.cache_control: Extra inputs are not
            # permitted". If a message has nothing cacheable it simply goes
            # unmarked; losing one breakpoint is cheap, a 400 kills the turn.
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in _CACHEABLE_BLOCK_TYPES:
                    continue
                block["cache_control"] = marker
                break
    return messages


def extract_openai_delta_text(content: Any) -> str:
    """Normalize a chat-completion delta's ``content`` field to plain text.

    Shared by every "openai_completions"-family provider (OpenAI Completions,
    GitHub Copilot Chat, ...). Almost always a plain string, but some
    non-standard OpenAI-compatible providers (Databricks-hosted Qwen3,
    gpt-oss reasoning models) send a list of typed content-part dicts instead
    when tools are present, e.g.
    ``[{"type": "reasoning", ...}, {"type": "text", "text": "hi"}]``. Only the
    "text" parts are user-facing content; anything else (reasoning parts,
    unrecognised part types) is skipped rather than guessed at.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


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
    GitHub Copilot, OpenAI Vertex, OpenRouter) plus Mistral. AudioContent and
    VideoContent are only reachable here for models a curator explicitly
    flagged with Modality.Audio/Modality.Video — the UI-level modality gate
    (InputHandler._on_submit) blocks submission before a message with
    unsupported media ever reaches this function, so a model that doesn't
    declare the modality never triggers these branches. As of this writing
    Video is only claimed by a subset of NVIDIA's own models and OpenRouter's
    proxied models — not the OpenAI/Copilot/Vertex/Mistral models themselves.

    VideoContent uses the same "video_url" data-URI shape NVIDIA NIM's Vision
    Language Model API documents (mirroring "image_url"); OpenRouter's
    video-capable backends accept the same OpenAI-style shape.
    """
    from tau.message.types import AudioContent, ImageContent, TextContent, VideoContent

    parts: list[dict[str, Any]] = []
    for item in content_items:
        match item:
            case TextContent():
                parts.append({"type": "text", "text": item.content})
            case ImageContent():
                for b64, mime in item.to_base64():
                    url = image_data_url(b64, mime)
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
            case VideoContent():
                for b64, mime in item.to_base64():
                    url = b64 if b64.startswith("http") else f"data:{mime};base64,{b64}"
                    parts.append({"type": "video_url", "video_url": {"url": url}})
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


def anthropic_thinking_params(model: Any, options: Any) -> dict[str, Any]:
    """Build the ``thinking`` (and, for adaptive models, ``output_config.effort``)
    request params for an Anthropic-Messages-compatible request.

    Dispatches on ``model.thinking_adaptive``: adaptive models (Opus 4.7+,
    Sonnet 4.6+/5, Fable 5) use ``thinking: {type: "adaptive"}`` with the level
    name passed as ``output_config.effort`` (or ``{type: "disabled"}`` for Off);
    older models (Haiku 4.5, Sonnet 4.5, Opus 4.5 and earlier) use
    ``thinking: {type: "enabled", budget_tokens: N}`` and have no notion of
    Off — omitting ``thinking`` entirely leaves it off by default.
    """
    from tau.inference.types import ThinkingBudgets, ThinkingLevel

    level = options.thinking_level
    if level is None:
        return {}

    if model.thinking_adaptive:
        if level == ThinkingLevel.Off:
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": level.value},
        }

    if level == ThinkingLevel.Off:
        return {}
    budgets = options.thinking_budgets or ThinkingBudgets()
    return {"thinking": {"type": "enabled", "budget_tokens": budgets.get(level)}}


#: Anthropic requires ``max_tokens``; this is what every Anthropic adapter sends
#: when ``LLMOptions.max_tokens`` is unset.
ANTHROPIC_DEFAULT_MAX_TOKENS = 8096


def anthropic_default_system_blocks(
    system: str | None,
    messages: list[dict[str, Any]],
    marker: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """One cached text block, or ``None`` so no ``system`` key is sent at all.

    Returns ``(system, messages)``; messages come back untouched. The Claude
    Code adapter substitutes its own builder, which prepends the OAuth identity
    blocks.
    """
    if not system:
        return None, messages
    block: dict[str, Any] = {"type": "text", "text": system}
    if marker is not None:
        block["cache_control"] = marker
    return [block], messages


def anthropic_build_params(
    model: Any,
    options: Any,
    system: str | None,
    messages: list[dict[str, Any]],
    *,
    tools: list[Any] | None = None,
    ephemeral_message_count: int = 0,
    system_blocks: Any = anthropic_default_system_blocks,
) -> dict[str, Any]:
    """Assemble the Anthropic Messages request payload.

    Shared by the three Anthropic adapters (direct, Vertex, Claude Code OAuth),
    which differ only in how the ``system`` array is built — hence the
    ``system_blocks`` hook, which takes ``(system, messages, marker)`` and
    returns the pair back.
    """
    marker = anthropic_cache_control(model.supports_long_cache_retention, options.cache_retention)
    params: dict[str, Any] = {
        "model": model.id,
        "messages": anthropic_apply_message_cache(
            messages, skip_tail=ephemeral_message_count, marker=marker
        ),
        "max_tokens": options.max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS,
    }
    if not model.thinking_suppresses_sampling:
        params["temperature"] = options.temperature
    system_value, params["messages"] = system_blocks(system, params["messages"], marker)
    if system_value is not None:
        params["system"] = system_value
    params.update(anthropic_thinking_params(model, options))

    if tools:
        tool_defs = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.schema.model_json_schema(),
            }
            for tool in tools
        ]
        # Cache the last tool definition to reduce repeated prompt-token charges.
        if marker is not None:
            tool_defs[-1]["cache_control"] = marker
        params["tools"] = tool_defs
    elif has_tool_history(params["messages"]):
        # Anthropic rejects the request outright if tool_use/tool_result blocks
        # exist anywhere in history but `tools` is absent — even an empty list
        # must be sent explicitly (e.g. after an extension calls
        # set_active_tools([]) mid-conversation).
        params["tools"] = []

    return params


def gemini_encode_signature(signature: bytes | None) -> str:
    """Encode an SDK thought signature for JSON-safe message persistence."""
    import base64

    return base64.b64encode(signature).decode("ascii") if signature else ""


def gemini_decode_signature(signature: object) -> bytes | None:
    """Decode a persisted thought signature for the Google Gen AI SDK."""
    import base64

    if not isinstance(signature, str) or not signature:
        return None
    return base64.b64decode(signature)


def gemini_messages_to_contents(
    messages: list[Any],
    *,
    distrust_thought_signatures: bool = False,
    include_call_ids: bool = True,
) -> tuple[str | None, list[Any]]:
    """Convert tau messages to ``(system, contents)`` for the Google Gen AI SDK.

    Shared by the Gemini and Vertex adapters. ``include_call_ids`` is the one
    behavioural difference between them: the Gemini API echoes the per-call
    ``id`` on functionCall/functionResponse, while Vertex correlates a response
    to its call by tool *name* and rejects the id field.
    """
    from google.genai import types as genai_types

    from tau.message.types import (
        AssistantMessage,
        AudioContent,
        FileContent,
        ImageContent,
        SystemMessage,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
        UserMessage,
        VideoContent,
    )

    def _blob_parts(item: Any, default_mime: str | None = None) -> list[Any]:
        return [
            genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type=mime or default_mime,
                    data=b64,  # type: ignore[arg-type]
                ),
            )
            for b64, mime in item.to_base64()
        ]

    system: str | None = None
    contents: list[Any] = []

    for msg in messages:
        match msg:
            case SystemMessage():
                system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            case UserMessage():
                parts: list[Any] = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            parts.append(genai_types.Part(text=item.content))  # type: ignore[arg-type]
                        case ImageContent():
                            parts.extend(_blob_parts(item, "image/png"))
                        case FileContent() | AudioContent() | VideoContent():
                            parts.extend(_blob_parts(item))
                if parts:
                    contents.append(genai_types.Content(role="user", parts=parts))  # type: ignore[arg-type]
            case AssistantMessage():
                parts = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            parts.append(genai_types.Part(text=item.content))  # type: ignore[arg-type]
                        case ThinkingContent():
                            parts.append(
                                genai_types.Part(
                                    text=item.content,
                                    thought=True,
                                    thought_signature=(
                                        None
                                        if distrust_thought_signatures
                                        else gemini_decode_signature(item.signature)
                                    ),
                                )
                            )
                        case ToolCallContent():
                            sig = (
                                None
                                if distrust_thought_signatures
                                else gemini_decode_signature(item.metadata.get("thought_signature"))
                            )
                            if sig is None:
                                # A functionCall part with no thoughtSignature is
                                # rejected (or silently degraded) by Gemini — not just
                                # gemini-3 — whenever history was replayed from a turn
                                # that never had one (a different provider, or a model
                                # switch). Fall back to a plain text description
                                # instead of sending an unsigned functionCall.
                                args_str = json.dumps(item.args, indent=2)
                                parts.append(
                                    genai_types.Part(
                                        text=f"[Tool Call: {item.name}]\nArguments: {args_str}"
                                    )
                                )
                            else:
                                call = (
                                    genai_types.FunctionCall(
                                        id=item.id, name=item.name, args=item.args
                                    )
                                    if include_call_ids
                                    else genai_types.FunctionCall(name=item.name, args=item.args)
                                )
                                parts.append(
                                    genai_types.Part(function_call=call, thought_signature=sig)
                                )
                if parts:
                    contents.append(genai_types.Content(role="model", parts=parts))  # type: ignore[arg-type]
            case ToolMessage():
                parts = []
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        # Gemini's FunctionResponse.response uses "output" for success
                        # and "error" for failure — Gemini 3 Flash Preview strictly
                        # rejects the older lenient providers' {"result", "isError"}
                        # shape (older Gemini models tolerated it).
                        key = "error" if content.is_error else "output"
                        if include_call_ids:
                            response = genai_types.FunctionResponse(
                                id=content.id,
                                name=content.tool_name or content.id,
                                response={key: tool_result_text(content)},
                                parts=gemini_function_response_parts(content),
                            )
                        else:
                            response = genai_types.FunctionResponse(
                                name=content.tool_name or content.id,
                                response={key: tool_result_text(content)},
                                parts=gemini_function_response_parts(content),
                            )
                        parts.append(genai_types.Part(function_response=response))
                if parts:
                    contents.append(genai_types.Content(role="user", parts=parts))  # type: ignore[arg-type]

    return system, contents


def image_data_url(b64: str, mime: str | None) -> str:
    """The value an OpenAI-shaped ``image_url`` field takes for one image.

    A remote URL passes through untouched; raw base64 is wrapped in a ``data:``
    URL. The PNG fallback matters because several providers reject a data URL
    with an empty media type outright.
    """
    return b64 if b64.startswith("http") else f"data:{mime or 'image/png'};base64,{b64}"


def openai_stop_reason(finish_reason: str | None) -> Any:
    """Map a chat-completions ``finish_reason`` to a tau StopReason.

    An unknown or absent reason degrades to Stop rather than raising — a
    provider inventing a new string should not fail the turn.
    """
    from tau.inference.types import StopReason

    return {
        "stop": StopReason.Stop,
        "length": StopReason.Length,
        "tool_calls": StopReason.ToolCalls,
        "content_filter": StopReason.ContentFilter,
    }.get(finish_reason or "", StopReason.Stop)


async def stream_openai_chat_events(
    sdk_stream: Any,
    *,
    cancelled: Callable[[], bool],
    extract_thinking: Callable[[Any], str | None] | None = None,
) -> AsyncGenerator[Any, None]:
    """Translate an OpenAI chat-completions SDK stream into tau LLMEvents.

    Shared by every provider speaking the chat-completions wire format
    (OpenAI, GitHub Copilot, Vertex's OpenAI endpoint, ...). Callers own the
    request — params, client, the ``on_response`` hook — and hand the parsed
    SDK stream here; everything from the first chunk to the final ``EndEvent``
    is identical between them.

    ``extract_thinking`` returns a provider's reasoning delta for a chunk delta,
    or None. Passing None means the provider emits no reasoning at all, so no
    Thinking events are produced.

    ``StartEvent`` is *not* emitted here — callers yield it before the request
    so an error while opening the connection still follows a started stream.
    """
    from tau.inference.types import (
        EndEvent,
        ErrorEvent,
        StopReason,
        TextDeltaEvent,
        TextEndEvent,
        TextStartEvent,
        ThinkingDeltaEvent,
        ThinkingEndEvent,
        ThinkingStartEvent,
        ToolCallDeltaEvent,
        ToolCallEndEvent,
        ToolCallStartEvent,
    )
    from tau.message.types import TextContent, ThinkingContent, ToolCallContent

    text_started = False
    text_buf = ""
    thinking_started = False
    thinking_buf = ""
    tool_started: dict[int, bool] = {}
    tool_bufs: dict[int, str] = {}
    tool_meta: dict[int, dict[str, str]] = {}
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    has_finish_reason = False
    stop_reason = StopReason.Stop

    def finalize_open_blocks() -> list[Any]:
        """Close any still-open thinking/text block and resolve any still-open
        tool call. Shared between the normal finish_reason branch and the
        no-finish-reason fallback below, so a stream that never sends
        finish_reason at all (observed from some non-standard OpenAI-compatible
        providers) still ends every block properly instead of leaving one
        dangling.
        """
        nonlocal thinking_started, thinking_buf, text_started, text_buf
        events: list[Any] = []
        if thinking_started:
            events.append(ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf)))
            thinking_started = False
            thinking_buf = ""
        if text_started:
            events.append(TextEndEvent(text=TextContent(content=text_buf)))
            text_started = False
            text_buf = ""
        for idx in sorted(tool_started):
            args = parse_tool_args(tool_bufs[idx].strip())
            events.append(
                ToolCallEndEvent(
                    tool_call=ToolCallContent(
                        id=tool_meta[idx]["id"],
                        name=tool_meta[idx]["name"],
                        args=args,
                    )
                )
            )
        tool_started.clear()
        tool_bufs.clear()
        tool_meta.clear()
        return events

    async for chunk in sdk_stream:
        if cancelled():
            yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
            return

        usage_data = getattr(chunk, "usage", None)
        if usage_data:
            input_tokens = getattr(usage_data, "prompt_tokens", 0) or 0
            output_tokens = getattr(usage_data, "completion_tokens", 0) or 0
            details = getattr(usage_data, "prompt_tokens_details", None)
            cache_read_tokens = getattr(details, "cached_tokens", 0) or 0

        choice = chunk.choices[0] if chunk.choices else None
        if choice is None:
            continue

        delta = choice.delta

        reasoning = extract_thinking(delta) if extract_thinking is not None else None
        if reasoning:
            if not thinking_started:
                yield ThinkingStartEvent(thinking=ThinkingContent(content=""))
                thinking_started = True
            thinking_buf += reasoning
            yield ThinkingDeltaEvent(thinking=ThinkingContent(content=reasoning))

        delta_text = extract_openai_delta_text(delta.content)
        if delta_text:
            # If thinking was happening, end it before starting text
            if thinking_started:
                yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                thinking_started = False
                thinking_buf = ""
            if not text_started:
                yield TextStartEvent(text=TextContent(content=""))
                text_started = True
            text_buf += delta_text
            yield TextDeltaEvent(text=TextContent(content=delta_text))

        if delta.tool_calls:
            # If thinking was happening, end it
            if thinking_started:
                yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                thinking_started = False
                thinking_buf = ""
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_started:
                    tool_started[idx] = True
                    tool_bufs[idx] = ""
                    tool_meta[idx] = {
                        "id": tc.id or "",
                        "name": tc.function.name or "" if tc.function else "",
                    }
                    yield ToolCallStartEvent(
                        tool_call=ToolCallContent(
                            id=tool_meta[idx]["id"],
                            name=tool_meta[idx]["name"],
                        )
                    )
                if tc.function and tc.function.arguments:
                    tool_bufs[idx] += tc.function.arguments
                    yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_meta[idx]["id"]))

        if choice.finish_reason:
            has_finish_reason = True
            for event in finalize_open_blocks():
                yield event
            stop_reason = openai_stop_reason(choice.finish_reason)

    if not has_finish_reason:
        # Some non-standard OpenAI-compatible providers never send a
        # finish_reason chunk at all — treat stream exhaustion as the implicit
        # stop instead of crashing. stop_reason keeps its StopReason.Stop
        # default: the honest answer when the provider never said why.
        for event in finalize_open_blocks():
            yield event

    # The usage-bearing chunk (stream_options.include_usage) arrives as a
    # separate final chunk with empty choices, *after* the finish_reason chunk —
    # yielding EndEvent inside the finish_reason branch above would capture 0
    # tokens whenever that chunk hadn't landed yet (routinely the case for
    # tool-calling turns). Yield only once the stream is fully drained so the
    # token counts reflect whatever arrived.
    yield EndEvent(
        reason=stop_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        input_tokens_include_cache_read=True,
    )
