from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.utils import (
    openai_gpt56_prompt_cache_options,
    openai_responses_function_call_output,
    parse_tool_args,
)
from tau.inference.model.types import Model
from tau.inference.types import (
    EndEvent,
    ErrorEvent,
    LLMContext,
    LLMEvent,
    LLMOptions,
    StartEvent,
    StopReason,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingLevel,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    normalize_structured_response_format,
)
from tau.message.types import (
    AssistantMessage,
    ImageContent,
    LLMMessage,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_THINKING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Minimal: "low",
    ThinkingLevel.Low: "low",
    ThinkingLevel.Medium: "medium",
    ThinkingLevel.High: "high",
    ThinkingLevel.XHigh: "high",
    ThinkingLevel.Max: "high",
}

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "max_output_tokens": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "content_filter": StopReason.ContentFilter,
}


def _extra_body_for(model: Model) -> dict[str, Any]:
    """Build the extra_body payload for fields not in the installed SDK's typed
    stream() signature (currently just prompt_cache_options).

    This adapter (api="openai_responses") is shared by openai, perplexity, xai,
    and bedrock (see tau/builtins/providers/text.py) — bedrock in particular
    proxies real OpenAI model ids (e.g. "openai.gpt-5.5") through AWS's Mantle
    gateway, a different backend than OpenAI's own servers with no guarantee it
    supports every new OpenAI-only request field. Gate on provider, not just
    model id, so a future "openai.gpt-5.6" on bedrock doesn't inherit an
    OpenAI-only field the proxy may reject — the same class of bug already hit
    and fixed on the Codex OAuth path.
    """
    extra_body: dict[str, Any] = {}
    if model.provider == "openai":
        cache_options = openai_gpt56_prompt_cache_options(model.id)
        if cache_options is not None:
            extra_body["prompt_cache_options"] = cache_options
    return extra_body


def _content_to_openai(content_items: list, supports_thinking: bool = True) -> list[dict[str, Any]]:
    """Convert typed message content items to OpenAI Responses API content parts.

    When supports_thinking is False, ThinkingContent is merged into the text
    content (thinking first, then text) so non-reasoning models receive full
    context without structured reasoning blocks they cannot accept.
    This merge is in-memory only; the session file is not affected.
    """
    if not supports_thinking:
        thinking_parts: list[str] = []
        text_parts: list[str] = []
        other_parts: list[dict[str, Any]] = []
        for item in content_items:
            match item:
                case ThinkingContent():
                    thinking_parts.append(item.content)
                case TextContent():
                    text_parts.append(item.content)
                case ImageContent():
                    for b64, mime in item.to_base64():
                        url = (
                            b64
                            if b64.startswith("http")
                            else f"data:{mime or 'image/png'};base64,{b64}"
                        )
                        other_parts.append({"type": "input_image", "image_url": url})
                # ToolCallContent is handled by _messages_to_input, which hoists
                # it out to a top-level function_call item — see the comment
                # on the supports_thinking=True branch below.
        parts: list[dict[str, Any]] = []
        if thinking_parts or text_parts:
            merged = "\n".join(thinking_parts + text_parts)
            parts.append({"type": "input_text", "text": merged})
        parts.extend(other_parts)
        return parts

    # ThinkingContent and ToolCallContent are intentionally not handled here:
    # a reasoning item and function_call are both top-level input items, not
    # nested inside a message's content array — _messages_to_input hoists them
    # out and interleaves them in original order (see the AssistantMessage
    # case there for why order matters for reasoning replay).
    parts = []
    for item in content_items:
        match item:
            case TextContent():
                parts.append({"type": "input_text", "text": item.content})
            case ImageContent():
                for b64, mime in item.to_base64():
                    url = (
                        b64
                        if b64.startswith("http")
                        else f"data:{mime or 'image/png'};base64,{b64}"
                    )
                    parts.append({"type": "input_image", "image_url": url})
    return parts


def _messages_to_input(
    messages: list[LLMMessage],
    supports_thinking: bool = True,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert a message list to OpenAI Responses API input items,
    extracting system as instructions.
    """
    instructions: str | None = None
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        match msg:
            case SystemMessage():
                system_text_parts = [c.content for c in msg.contents if isinstance(c, TextContent)]
                instructions = "\n".join(system_text_parts)
            case ToolMessage():
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        input_items.append(
                            {
                                "type": "function_call_output",
                                "call_id": content.id,
                                "output": openai_responses_function_call_output(content),
                            }
                        )
            case UserMessage():
                parts = _content_to_openai(msg.contents, supports_thinking=supports_thinking)
                if parts:
                    input_items.append({"role": "user", "content": parts})
            case AssistantMessage() if not supports_thinking:
                parts = _content_to_openai(msg.contents, supports_thinking=False)
                if parts:
                    input_items.append({"role": "assistant", "content": parts})
                for content in msg.contents:
                    if isinstance(content, ToolCallContent):
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": content.id,
                                "name": content.name,
                                "arguments": json.dumps(content.args),
                            }
                        )
            case AssistantMessage():
                # reasoning and function_call are top-level input items in the
                # Responses API, not nested inside a message's content array, and
                # a reasoning item must immediately precede the item it justified
                # (the tool call or message that followed it in the original
                # response). So this walks msg.contents in original order instead
                # of grouping all text first, flushing buffered text parts before
                # emitting any top-level item.
                text_parts: list[dict[str, Any]] = []
                for content in msg.contents:
                    match content:
                        case ThinkingContent():
                            # Only a signed block can be replayed statelessly
                            # (store: false) — the signature is the full raw
                            # reasoning item (including encrypted_content)
                            # captured at stream time. Drop unsigned blocks
                            # (older sessions, or left over from a provider/model
                            # switch) instead of sending a malformed reasoning item.
                            if content.signature:
                                if text_parts:
                                    input_items.append({"role": "assistant", "content": text_parts})
                                    text_parts = []
                                try:
                                    reasoning_item = json.loads(content.signature)
                                except (TypeError, ValueError):
                                    reasoning_item = None
                                if isinstance(reasoning_item, dict):
                                    input_items.append(reasoning_item)
                        case TextContent():
                            text_parts.append({"type": "input_text", "text": content.content})
                        case ImageContent():
                            for b64, mime in content.to_base64():
                                url = (
                                    b64
                                    if b64.startswith("http")
                                    else f"data:{mime or 'image/png'};base64,{b64}"
                                )
                                text_parts.append({"type": "input_image", "image_url": url})
                        case ToolCallContent():
                            if text_parts:
                                input_items.append({"role": "assistant", "content": text_parts})
                                text_parts = []
                            input_items.append(
                                {
                                    "type": "function_call",
                                    "call_id": content.id,
                                    "name": content.name,
                                    "arguments": json.dumps(content.args),
                                }
                            )
                if text_parts:
                    input_items.append({"role": "assistant", "content": text_parts})

    return instructions, input_items


def _text_format(response_format: Any | None) -> dict[str, Any] | None:
    """Convert response_format to the OpenAI Responses API text.format structure."""
    structured = normalize_structured_response_format(response_format)
    if structured is None:
        return None
    return {
        "format": {
            "type": "json_schema",
            "name": structured.name,
            "schema": structured.schema,
            "strict": structured.strict,
        }
    }


class OpenAIResponsesAPI(BaseAPI):
    """Streaming LLM API adapter for the OpenAI Responses API (o-series / GPT-4o)."""

    def __init__(self, options: LLMOptions) -> None:
        """Initialise the AsyncOpenAI client with the supplied options."""
        super().__init__(options)
        self._client = AsyncOpenAI(
            api_key=options.api_key or "placeholder",
            base_url=options.base_url,
            default_headers=options.headers,
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(
        self,
        model: Model,
        instructions: str | None,
        input_items: list,
        tools: list[Tool] | None = None,
    ) -> dict[str, Any]:
        """Assemble the OpenAI Responses API request payload."""
        params: dict[str, Any] = {
            "model": model.id,
            "input": input_items,
            "temperature": self.options.temperature,
        }
        if instructions:
            params["instructions"] = instructions
        if self.options.max_tokens is not None:
            params["max_output_tokens"] = self.options.max_tokens
        if (
            self.options.thinking_level is not None
            and self.options.thinking_level != ThinkingLevel.Off
        ):
            params["reasoning"] = {"effort": _THINKING_EFFORT[self.options.thinking_level]}
            # Whenever reasoning is engaged, request the encrypted reasoning
            # item back (store: False keeps this stateless — no server-side
            # conversation state, matching every other provider/turn in this
            # session) so it can be captured and replayed on the next turn
            # (see _messages_to_input's AssistantMessage case). Without this,
            # reasoning never reaches the model on subsequent turns.
            params["store"] = False
            params["include"] = ["reasoning.encrypted_content"]

        if tools:
            tool_defs = []
            for tool in tools:
                schema = tool.schema.model_json_schema()
                tool_def: dict[str, Any] = {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                }
                tool_defs.append(tool_def)
            params["tools"] = tool_defs

        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        """Stream LLMEvents from the OpenAI Responses API."""
        if self.options.api_key:
            self._client.api_key = self.options.api_key
        instructions, input_items = _messages_to_input(
            context.messages, supports_thinking=bool(model.thinking)
        )
        params = self._build_params(model, instructions, input_items, tools=context.tools or None)
        text_format = _text_format(context.response_format)
        if text_format is not None:
            params["text"] = text_format

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        # prompt_cache_options isn't in the installed SDK's typed stream()
        # signature yet, so it has to ride in extra_body rather than be spread
        # as a keyword argument (same pattern as openai_completions.py).
        extra_body = _extra_body_for(model)

        # Keyed by the response item's own id (event.item_id in the arguments
        # delta/done events), mapping to (call_id, name). item_id and call_id
        # are two distinct identifiers — OpenAI's backend happens to make them
        # equal, but other Responses-API-compatible backends (e.g. xAI's Grok
        # CLI proxy) don't, so they must be tracked separately.
        tool_calls: dict[str, tuple[str, str]] = {}
        # Final reasoning summary text, keyed by item id, buffered until
        # response.output_item.done delivers the full item (id + summary +
        # encrypted_content when requested via include=["reasoning.encrypted_content"])
        # so ThinkingEndEvent can carry both the text and the replay signature at once.
        reasoning_text_by_item: dict[str, str] = {}
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_tokens = 0
        _cache_write_tokens = 0

        yield StartEvent()

        async with self._client.responses.stream(**params, extra_body=extra_body or None) as stream:
            async for event in stream:
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                etype = event.type

                if etype == "response.output_item.added":
                    item = event.item  # type: ignore[union-attr]
                    if item.type == "message":
                        yield TextStartEvent(text=TextContent(content=""))
                    elif item.type == "reasoning":
                        yield ThinkingStartEvent(thinking=None)
                    elif item.type == "function_call":
                        tool_calls[item.id] = (item.call_id, item.name)  # type: ignore[union-attr,index]
                        yield ToolCallStartEvent(
                            tool_call=ToolCallContent(id=item.call_id, name=item.name)  # type: ignore[union-attr,arg-type]
                        )

                elif etype == "response.output_text.delta":
                    yield TextDeltaEvent(text=TextContent(content=event.delta))  # type: ignore[union-attr]

                elif etype == "response.output_text.done":
                    yield TextEndEvent(text=TextContent(content=event.text))  # type: ignore[union-attr]

                elif etype == "response.reasoning_summary_text.delta":
                    yield ThinkingDeltaEvent(thinking=ThinkingContent(content=event.delta))  # type: ignore[union-attr]

                elif etype == "response.reasoning_summary_text.done":
                    reasoning_text_by_item[event.item_id] = event.text  # type: ignore[union-attr]

                elif etype == "response.output_item.done":
                    item = event.item  # type: ignore[union-attr]
                    if item.type == "reasoning":
                        content = reasoning_text_by_item.pop(item.id, "")  # type: ignore[union-attr]
                        # exclude_unset=True: the typed SDK model declares fields
                        # like `content` with a default of None, and a plain
                        # model_dump() would materialize those as an explicit
                        # `"content": null` even when the server never sent that
                        # key at all — an addition relative to the raw wire item
                        # that some backends' stateless-replay validation (e.g.
                        # xAI's Grok CLI proxy, which round-trips this signature
                        # as a "compaction blob") rejects as "modified".
                        signature = json.dumps(item.model_dump(mode="json", exclude_unset=True))  # type: ignore[union-attr]
                        yield ThinkingEndEvent(
                            thinking=ThinkingContent(content=content, signature=signature)
                        )

                elif etype == "response.function_call_arguments.delta":
                    call_id, _ = tool_calls.get(event.item_id, (event.item_id, ""))  # type: ignore[union-attr]
                    yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=call_id))

                elif etype == "response.function_call_arguments.done":
                    call_id, name = tool_calls.get(event.item_id, (event.item_id, ""))  # type: ignore[union-attr]
                    args_str = event.arguments.strip()  # type: ignore[union-attr]
                    args = parse_tool_args(args_str)

                    yield ToolCallEndEvent(
                        tool_call=ToolCallContent(id=call_id, name=name, args=args)
                    )

                elif etype == "response.done":
                    resp = event.response  # type: ignore[union-attr]
                    u = getattr(resp, "usage", None)
                    if u:
                        _input_tokens = getattr(u, "input_tokens", 0) or 0
                        _output_tokens = getattr(u, "output_tokens", 0) or 0
                        _details = getattr(u, "input_tokens_details", None)
                        _cache_read_tokens = getattr(_details, "cached_tokens", 0) or 0
                        _cache_write_tokens = getattr(_details, "cache_write_tokens", 0) or 0
                    stop_reason = _STOP_REASON.get(
                        getattr(resp, "stop_reason", None) or "",
                        StopReason.Stop,
                    )
                    yield EndEvent(
                        reason=stop_reason,
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        cache_read_tokens=_cache_read_tokens,
                        cache_write_tokens=_cache_write_tokens,
                        input_tokens_include_cache_read=True,
                    )

                elif etype == "error":
                    from tau.inference.utils import classify_error

                    err_msg = str(getattr(event, "message", None) or event)
                    classified = classify_error(ValueError(err_msg))
                    yield ErrorEvent(reason=StopReason.Error, error=err_msg, kind=classified.kind)
