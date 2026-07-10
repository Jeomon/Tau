from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from tau.inference.api.text import dialect
from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.utils import (
    openai_messages_to_chat,
    openai_response_format,
    parse_tool_args,
    strict_json_schema,
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
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from tau.message.types import (
    TextContent,
    ThinkingContent,
    ToolCallContent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "content_filter": StopReason.ContentFilter,
}


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip fields that trip up strict OpenAI-compatible APIs (title, $defs, etc.)."""
    result: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("title", "$schema"):
            continue
        if k == "anyOf" and isinstance(v, list):
            non_null = [
                _clean_schema(s) if isinstance(s, dict) else s for s in v if s != {"type": "null"}
            ]
            if len(non_null) == 1:
                result.update(non_null[0])
            else:
                result[k] = non_null
        elif isinstance(v, dict):
            result[k] = _clean_schema(v)
        elif isinstance(v, list):
            result[k] = [_clean_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


class OpenAICompletionsAPI(BaseAPI):
    """Streaming LLM API adapter for the OpenAI Chat Completions endpoint."""

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
        self, model: Model, messages: list[dict[str, Any]], tools: list[Tool] | None = None
    ) -> dict[str, Any]:
        """Assemble the OpenAI Chat Completions request payload."""
        params: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_completion_tokens"] = self.options.max_tokens

        if tools:
            tool_defs = []
            for tool in tools:
                schema = _clean_schema(tool.schema.model_json_schema())
                function: dict[str, Any] = {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                }
                if tool.strict:
                    function["parameters"] = strict_json_schema(schema)
                    function["strict"] = True
                tool_defs.append({"type": "function", "function": function})
            params["tools"] = tool_defs
            params["tool_choice"] = "auto"

        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        """Stream LLMEvents from the OpenAI Chat Completions API."""
        if self.options.api_key:
            self._client.api_key = self.options.api_key
        chat_messages = openai_messages_to_chat(context.messages, model)
        if context.system_prompt:
            chat_messages = [{"role": "system", "content": context.system_prompt}] + chat_messages
        params = self._build_params(model, chat_messages, tools=context.tools or None)
        response_format = openai_response_format(context.response_format)
        if response_format is not None:
            params["response_format"] = response_format

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        # Dialect-specific reasoning params (chat_template_kwargs, thinking, etc.)
        # aren't part of the SDK's typed create() signature, so they must ride in
        # extra_body rather than be spread as keyword arguments.
        extra_body = {
            **dialect.build_reasoning_request_params(model, self.options),
            **(self.options.extra_params or {}),
        }

        text_started = False
        text_buf = ""
        thinking_started = False
        thinking_buf = ""
        # Tool-call accumulation state keyed by delta index
        # (OpenAI streams partial tool calls per-index).
        tool_started: dict[int, bool] = {}
        tool_bufs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, str]] = {}
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_tokens = 0
        has_finish_reason = False
        stop_reason = StopReason.Stop

        yield StartEvent()

        # async with closes the SDK stream (and its httpx response) on every
        # exit path — cancellation return or an upstream GeneratorExit — instead
        # of leaving it to the GC asyncgen finalizer.
        async with await self._client.chat.completions.create(
            **params,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
        ) as sdk_stream:
            async for chunk in sdk_stream:
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                usage_data = getattr(chunk, "usage", None)
                if usage_data:
                    _input_tokens = getattr(usage_data, "prompt_tokens", 0) or 0
                    _output_tokens = getattr(usage_data, "completion_tokens", 0) or 0
                    _details = getattr(usage_data, "prompt_tokens_details", None)
                    _cache_read_tokens = getattr(_details, "cached_tokens", 0) or 0
                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue

                delta = choice.delta

                reasoning = dialect.extract_thinking_delta(delta)
                if reasoning:
                    if not thinking_started:
                        yield ThinkingStartEvent(thinking=ThinkingContent(content=""))
                        thinking_started = True
                    thinking_buf += reasoning
                    yield ThinkingDeltaEvent(thinking=ThinkingContent(content=reasoning))

                if delta.content:
                    # If thinking was happening, end it before starting text
                    if thinking_started:
                        yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                        thinking_started = False
                        thinking_buf = ""

                    if not text_started:
                        yield TextStartEvent(text=TextContent(content=""))
                        text_started = True
                    text_buf += delta.content
                    yield TextDeltaEvent(text=TextContent(content=delta.content))

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
                            yield ToolCallDeltaEvent(
                                tool_call=ToolCallContent(id=tool_meta[idx]["id"])
                            )

                if choice.finish_reason:
                    has_finish_reason = True
                    if thinking_started:
                        yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                        thinking_started = False
                        thinking_buf = ""

                    if text_started:
                        yield TextEndEvent(text=TextContent(content=text_buf))
                        text_started = False
                        text_buf = ""

                    for idx in sorted(tool_started):
                        args_str = tool_bufs[idx].strip()
                        args = parse_tool_args(args_str)

                        yield ToolCallEndEvent(
                            tool_call=ToolCallContent(
                                id=tool_meta[idx]["id"],
                                name=tool_meta[idx]["name"],
                                args=args,
                            )
                        )
                    tool_started.clear()
                    tool_bufs.clear()
                    tool_meta.clear()

                    stop_reason = _STOP_REASON.get(choice.finish_reason, StopReason.Stop)

        if not has_finish_reason:
            raise RuntimeError("Stream ended without finish_reason")

        # The usage-bearing chunk (stream_options.include_usage) arrives as a
        # separate final chunk with empty choices, *after* the finish_reason
        # chunk — yielding EndEvent inside the finish_reason branch above would
        # capture 0 tokens whenever that chunk hadn't landed yet (routinely the
        # case for tool-calling turns). Yield only once the stream is fully
        # drained so _input_tokens/_output_tokens reflect whatever arrived.
        yield EndEvent(
            reason=stop_reason,
            input_tokens=_input_tokens,
            output_tokens=_output_tokens,
            cache_read_tokens=_cache_read_tokens,
            input_tokens_include_cache_read=True,
        )
