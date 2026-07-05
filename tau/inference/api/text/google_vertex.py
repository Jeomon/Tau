from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types as genai_types

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.utils import gemini_tool_schema, tool_result_text
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

__all__ = ["GoogleVertexAPI"]

_tool_call_counter = 0

_STOP_REASON: dict[str, StopReason] = {
    "STOP": StopReason.Stop,
    "MAX_TOKENS": StopReason.Length,
    "SAFETY": StopReason.ContentFilter,
    "RECITATION": StopReason.ContentFilter,
}


def _build_client(options: LLMOptions) -> genai.Client:
    extra = options.extra_params or {}

    api_key = options.api_key or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if api_key and not _is_placeholder(api_key):
        return genai.Client(vertexai=True, api_key=api_key)

    project = (
        extra.get("project")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    location = extra.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION")

    if not project:
        raise ValueError(
            "Vertex AI requires a project ID. "
            "Set GOOGLE_CLOUD_PROJECT or pass project in extra_params."
        )
    if not location:
        raise ValueError(
            "Vertex AI requires a location. "
            "Set GOOGLE_CLOUD_LOCATION or pass location in extra_params."
        )

    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            gac,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return genai.Client(
            vertexai=True, project=project, location=location, credentials=credentials
        )

    # Fall back to Application Default Credentials
    return genai.Client(vertexai=True, project=project, location=location)


def _is_placeholder(value: str) -> bool:
    return value.startswith("<") and value.endswith(">")


def _messages_to_gemini(
    messages: list[LLMMessage],
) -> tuple[str | None, list[genai_types.Content]]:
    system: str | None = None
    contents: list[genai_types.Content] = []

    for msg in messages:
        match msg:
            case SystemMessage():
                system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            case UserMessage():
                parts: list[genai_types.Part] = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            parts.append(genai_types.Part(text=item.content))  # type: ignore[arg-type]
                        case ImageContent():
                            for b64, mime in item.to_base64():
                                parts.append(
                                    genai_types.Part(
                                        inline_data=genai_types.Blob(
                                            mime_type=mime or "image/png",
                                            data=b64,  # type: ignore[arg-type]
                                        ),
                                    )
                                )
                if parts:
                    contents.append(genai_types.Content(role="user", parts=parts))  # type: ignore[arg-type]
            case AssistantMessage():
                parts = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            parts.append(genai_types.Part(text=item.content))  # type: ignore[arg-type]
                        case ToolCallContent():
                            parts.append(
                                genai_types.Part(
                                    function_call=genai_types.FunctionCall(
                                        name=item.name,
                                        args=item.args,
                                    ),
                                )
                            )
                if parts:
                    contents.append(genai_types.Content(role="model", parts=parts))  # type: ignore[arg-type]
            case ToolMessage():
                parts = []
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        parts.append(
                            genai_types.Part(
                                function_response=genai_types.FunctionResponse(
                                    name=content.id,
                                    response={"result": tool_result_text(content)},
                                ),
                            )
                        )
                if parts:
                    contents.append(genai_types.Content(role="user", parts=parts))  # type: ignore[arg-type]

    return system, contents


def _response_schema(response_format: Any | None) -> dict[str, Any] | None:
    structured = normalize_structured_response_format(response_format)
    return structured.schema if structured is not None else None


class GoogleVertexAPI(BaseAPI):
    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        self._client = _build_client(options)

    def _build_config(
        self,
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
            budgets = self.options.thinking_budgets or ThinkingBudgets()
            budget = budgets.get(self.options.thinking_level)
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
        system, contents = _messages_to_gemini(context.messages)
        config = self._build_config(
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

        thinking_index = 0
        tool_index = 0
        text_started = False
        thinking_started = False
        text_buf = ""
        thinking_buf = ""
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_tokens = 0
        seen_tool_ids: set[str] = set()

        yield StartEvent()

        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=model.id,
                contents=contents,  # type: ignore[arg-type]
                config=config,
            ):
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return

                um = getattr(chunk, "usage_metadata", None)
                if um:
                    _input_tokens = getattr(um, "prompt_token_count", 0) or 0
                    _output_tokens = getattr(um, "candidates_token_count", 0) or 0
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
                            yield ThinkingDeltaEvent(
                                thinking=ThinkingContent(content=part.text)  # type: ignore[arg-type]
                            )
                        elif part.text:
                            if thinking_started:
                                yield ThinkingEndEvent(
                                    thinking=ThinkingContent(content=thinking_buf)  # type: ignore[arg-type]
                                )
                                thinking_started = False
                                thinking_index += 1
                                thinking_buf = ""
                            if not text_started:
                                yield TextStartEvent(text=TextContent(content=""))  # type: ignore[arg-type]
                                text_started = True
                            text_buf += part.text
                            yield TextDeltaEvent(text=TextContent(content=part.text))  # type: ignore[arg-type]
                        elif part.function_call:
                            global _tool_call_counter
                            fc = part.function_call
                            tool_name = fc.name or ""
                            provided_id = fc.id
                            if provided_id and provided_id not in seen_tool_ids:
                                tool_id = provided_id
                            else:
                                _tool_call_counter += 1
                                tool_id = (
                                    f"{tool_name}_{int(time.time() * 1000)}_{_tool_call_counter}"
                                )
                            seen_tool_ids.add(tool_id)
                            args_str = json.dumps(dict(fc.args)) if fc.args else ""
                            yield ToolCallStartEvent(
                                tool_call=ToolCallContent(id=tool_id, name=tool_name)  # type: ignore[arg-type]
                            )
                            yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id))  # type: ignore[arg-type]
                            yield ToolCallEndEvent(
                                tool_call=ToolCallContent(  # type: ignore[arg-type]
                                    id=tool_id,
                                    name=tool_name,
                                    args=json.loads(args_str) if args_str else {},
                                )
                            )
                            tool_index += 1

                finish_reason = getattr(candidate, "finish_reason", None)
                if finish_reason and str(finish_reason) not in ("", "FINISH_REASON_UNSPECIFIED"):
                    if thinking_started:
                        yield ThinkingEndEvent(
                            thinking=ThinkingContent(content=thinking_buf)  # type: ignore[arg-type]
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

        except Exception as exc:
            from tau.inference.utils import classify_error

            classified = classify_error(exc)
            yield ErrorEvent(reason=StopReason.Error, error=str(exc), kind=classified.kind)
            return

        if thinking_started:
            yield ThinkingEndEvent(
                thinking=ThinkingContent(content=thinking_buf)  # type: ignore[arg-type]
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
