from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types as genai_types

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    gemini_encode_signature,
    gemini_messages_to_contents,
    gemini_tool_schema,
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
    ThinkingBudgets,
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
    LLMMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_STOP_REASON: dict[str, StopReason] = {
    "STOP": StopReason.Stop,
    "MAX_TOKENS": StopReason.Length,
    "SAFETY": StopReason.ContentFilter,
    "RECITATION": StopReason.ContentFilter,
}


def _encode_signature(signature: bytes | None) -> str:
    return gemini_encode_signature(signature)


def _messages_to_gemini(
    messages: list[LLMMessage],
    *,
    distrust_thought_signatures: bool = False,
) -> tuple[str | None, list[genai_types.Content]]:
    return gemini_messages_to_contents(
        messages,
        distrust_thought_signatures=distrust_thought_signatures,
        include_call_ids=True,
    )


def _response_schema(response_format: Any | None) -> dict[str, Any] | None:
    structured = normalize_structured_response_format(response_format)
    return structured.schema if structured is not None else None


_GEMINI3_THINKING_LEVEL: dict[ThinkingLevel, genai_types.ThinkingLevel] = {
    ThinkingLevel.Minimal: genai_types.ThinkingLevel.MINIMAL,
    ThinkingLevel.Low: genai_types.ThinkingLevel.LOW,
    ThinkingLevel.Medium: genai_types.ThinkingLevel.MEDIUM,
    ThinkingLevel.High: genai_types.ThinkingLevel.HIGH,
    ThinkingLevel.XHigh: genai_types.ThinkingLevel.HIGH,
    ThinkingLevel.Max: genai_types.ThinkingLevel.HIGH,
}


class GeminiGenerateAPI(BaseAPI):
    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        http_options = (
            genai_types.HttpOptions(base_url=options.base_url, headers=options.headers or None)
            if options.base_url or options.headers
            else None
        )
        self._client = genai.Client(api_key=options.api_key, http_options=http_options)

    async def aclose(self) -> None:
        # genai.Client.close() only tears down the synchronous client; the
        # async interface (.aio, used by _stream) needs its own aclose().
        await self._client.aio.aclose()

    def _build_config(
        self,
        uses_thinking_level: bool = False,
        tools: list[Tool] | None = None,
        response_format: Any | None = None,
    ) -> genai_types.GenerateContentConfig:
        params: dict[str, Any] = {
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_output_tokens"] = self.options.max_tokens
        schema = _response_schema(response_format)
        if schema is not None:
            params["response_mime_type"] = "application/json"
            params["response_schema"] = schema

        if (
            self.options.thinking_level is not None
            and self.options.thinking_level != ThinkingLevel.Off
        ):
            if uses_thinking_level:
                # Gemini 3 models are designed around a coarse thinking_level
                # (MINIMAL/LOW/MEDIUM/HIGH), not an explicit token budget — sending
                # thinking_budget instead produces much shorter test-time
                # computation than the requested level actually calls for.
                params["thinking_config"] = genai_types.ThinkingConfig(
                    thinking_level=_GEMINI3_THINKING_LEVEL.get(
                        self.options.thinking_level, genai_types.ThinkingLevel.HIGH
                    ),
                    include_thoughts=True,
                )
            else:
                budgets = self.options.thinking_budgets or ThinkingBudgets()
                budget = budgets.get(self.options.thinking_level)
                if budget is not None:
                    params["thinking_config"] = genai_types.ThinkingConfig(
                        thinking_budget=budget,
                        include_thoughts=True,
                    )

        if tools:
            params["tools"] = [
                genai_types.Tool(
                    function_declarations=[
                        genai_types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters=gemini_tool_schema(  # type: ignore[arg-type]
                                t.schema.model_json_schema()
                            ),
                        )
                        for t in tools
                    ]
                )
            ]

        return genai_types.GenerateContentConfig(**params)

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        distrust_sigs = self.options.distrust_thought_signatures
        system, contents = _messages_to_gemini(
            context.messages, distrust_thought_signatures=distrust_sigs
        )
        config = self._build_config(
            uses_thinking_level=model.thinking_uses_level,
            tools=context.tools or None,
            response_format=context.response_format,
        )
        effective_system = context.system_prompt or system
        if effective_system:
            config.system_instruction = effective_system

        if self.options.on_payload:
            payload = {"config": config, "contents": contents}
            modified = self.options.on_payload(payload)
            if modified is not None:
                config = modified.get("config", config)
                contents = modified.get("contents", contents)

        # Read live, not at client-construction time: a `before_provider_request`
        # extension hook may have mutated `self.options.headers` in place just
        # before this call. Merges with (doesn't replace) the client-level
        # headers set in __init__ — see patch_http_options in the SDK.
        if self.options.headers:
            config.http_options = genai_types.HttpOptions(headers=self.options.headers)

        thinking_index = 0
        tool_index = 0
        text_started = False
        thinking_started = False
        text_buf = ""
        thinking_buf = ""
        thinking_signature = ""
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_tokens = 0

        yield StartEvent()

        response_reported = False
        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=model.id,
                contents=contents,  # type: ignore[arg-type]
                config=config,
            ):
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                # Any chunk reaching here implies HTTP 200 — the SDK raises
                # APIError immediately on a non-2xx response instead of
                # yielding it, so there's no separate status to read.
                if not response_reported and self.options.on_response:
                    response_reported = True
                    http_response = getattr(chunk, "sdk_http_response", None)
                    headers = dict(getattr(http_response, "headers", None) or {})
                    self.options.on_response(APIResponse(200, headers))
                um = getattr(chunk, "usage_metadata", None)
                if um:
                    # tool_use_prompt_token_count covers tool-result tokens fed back
                    # as input; thoughts_token_count is reported separately from
                    # candidates_token_count for thinking models, so both must be
                    # added in or a thinking turn's usage is undercounted.
                    _input_tokens = (getattr(um, "prompt_token_count", 0) or 0) + (
                        getattr(um, "tool_use_prompt_token_count", 0) or 0
                    )
                    _output_tokens = (getattr(um, "candidates_token_count", 0) or 0) + (
                        getattr(um, "thoughts_token_count", 0) or 0
                    )
                    _cache_read_tokens = getattr(um, "cached_content_token_count", 0) or 0

                if not chunk.candidates:
                    continue

                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if getattr(part, "thought", False) and part.text:
                            if not thinking_started:
                                yield ThinkingStartEvent(thinking=None)
                                thinking_started = True
                            thinking_buf += part.text
                            if part.thought_signature:
                                thinking_signature = _encode_signature(part.thought_signature)
                            yield ThinkingDeltaEvent(thinking=ThinkingContent(content=part.text))  # type: ignore[arg-type]
                        elif part.text:
                            if thinking_started:
                                yield ThinkingEndEvent(
                                    thinking=ThinkingContent(
                                        content=thinking_buf,
                                        signature=thinking_signature,
                                    )
                                )
                                thinking_started = False
                                thinking_index += 1
                                thinking_buf = ""
                                thinking_signature = ""
                            if not text_started:
                                yield TextStartEvent(text=TextContent(content=""))  # type: ignore[arg-type]
                                text_started = True
                            text_buf += part.text
                            yield TextDeltaEvent(text=TextContent(content=part.text))  # type: ignore[arg-type]
                        elif part.function_call:
                            fc = part.function_call
                            tool_name = fc.name or ""
                            tool_id = fc.id or tool_name
                            args_str = json.dumps(dict(fc.args)) if fc.args else ""
                            metadata = (
                                {"thought_signature": _encode_signature(part.thought_signature)}
                                if part.thought_signature
                                else {}
                            )
                            yield ToolCallStartEvent(
                                tool_call=ToolCallContent(  # type: ignore[arg-type]
                                    id=tool_id,
                                    name=tool_name,
                                    metadata=metadata,
                                )
                            )
                            yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id))  # type: ignore[arg-type]
                            yield ToolCallEndEvent(
                                tool_call=ToolCallContent(  # type: ignore[arg-type]
                                    id=tool_id,  # type: ignore[arg-type]
                                    name=tool_name,
                                    args=json.loads(args_str) if args_str else {},
                                    metadata=metadata,
                                )
                            )
                            tool_index += 1

                finish_reason = getattr(candidate, "finish_reason", None)
                if finish_reason and str(finish_reason) not in ("", "FINISH_REASON_UNSPECIFIED"):
                    if thinking_started:
                        yield ThinkingEndEvent(
                            thinking=ThinkingContent(
                                content=thinking_buf,
                                signature=thinking_signature,
                            )
                        )
                    if text_started:
                        yield TextEndEvent(text=TextContent(content=text_buf))  # type: ignore[arg-type]
                    reason_str = (
                        finish_reason.name if hasattr(finish_reason, "name") else str(finish_reason)
                    )
                    stop = (
                        StopReason.ToolCalls
                        if tool_index > 0
                        else _STOP_REASON.get(reason_str, StopReason.Stop)
                    )
                    yield EndEvent(
                        reason=stop,
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        cache_read_tokens=_cache_read_tokens,
                        input_tokens_include_cache_read=True,
                    )
                    return

        except Exception:
            # Propagate so TextLLM.stream can classify the error and drive its
            # retry/backoff and OAuth-recovery logic; yielding an ErrorEvent
            # here would swallow the classification (service.py handles both
            # pre-stream failures and mid-stream errors).
            raise

        if thinking_started:
            yield ThinkingEndEvent(
                thinking=ThinkingContent(
                    content=thinking_buf,
                    signature=thinking_signature,
                )
            )
        if text_started:
            yield TextEndEvent(text=TextContent(content=text_buf))  # type: ignore[arg-type]
        yield EndEvent(
            reason=StopReason.Stop,
            input_tokens=_input_tokens,
            output_tokens=_output_tokens,
            cache_read_tokens=_cache_read_tokens,
            input_tokens_include_cache_read=True,
        )
