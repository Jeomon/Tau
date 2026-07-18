from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types as genai_types

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    gemini_function_response_parts,
    gemini_tool_schema,
    tool_result_text,
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
    AssistantMessage,
    AudioContent,
    FileContent,
    ImageContent,
    LLMMessage,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
    VideoContent,
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


def _encode_signature(signature: bytes | None) -> str:
    """Encode an SDK thought signature for JSON-safe message persistence."""
    return base64.b64encode(signature).decode("ascii") if signature else ""


def _decode_signature(signature: object) -> bytes | None:
    """Decode a persisted thought signature for the Google Gen AI SDK."""
    if not isinstance(signature, str) or not signature:
        return None
    return base64.b64decode(signature)


def _messages_to_gemini(
    messages: list[LLMMessage],
    *,
    distrust_thought_signatures: bool = False,
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
                        case FileContent():
                            for b64, mime in item.to_base64():
                                parts.append(
                                    genai_types.Part(
                                        inline_data=genai_types.Blob(
                                            mime_type=mime,
                                            data=b64,  # type: ignore[arg-type]
                                        ),
                                    )
                                )
                        case AudioContent():
                            for b64, mime in item.to_base64():
                                parts.append(
                                    genai_types.Part(
                                        inline_data=genai_types.Blob(
                                            mime_type=mime,
                                            data=b64,  # type: ignore[arg-type]
                                        ),
                                    )
                                )
                        case VideoContent():
                            for b64, mime in item.to_base64():
                                parts.append(
                                    genai_types.Part(
                                        inline_data=genai_types.Blob(
                                            mime_type=mime,
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
                        case ThinkingContent():
                            parts.append(
                                genai_types.Part(
                                    text=item.content,
                                    thought=True,
                                    thought_signature=(
                                        None
                                        if distrust_thought_signatures
                                        else _decode_signature(item.signature)
                                    ),
                                )
                            )
                        case ToolCallContent():
                            sig = (
                                None
                                if distrust_thought_signatures
                                else _decode_signature(item.metadata.get("thought_signature"))
                            )
                            if sig is None:
                                # A functionCall part with no thoughtSignature is
                                # rejected outright — e.g. history replayed from a
                                # turn that never had one (a different provider, or
                                # a model switch). Fall back to a plain text
                                # description instead of sending an unsigned call.
                                args_str = json.dumps(item.args, indent=2)
                                parts.append(
                                    genai_types.Part(
                                        text=f"[Tool Call: {item.name}]\nArguments: {args_str}"
                                    )
                                )
                            else:
                                parts.append(
                                    genai_types.Part(
                                        function_call=genai_types.FunctionCall(
                                            name=item.name,
                                            args=item.args,
                                        ),
                                        thought_signature=sig,
                                    )
                                )
                if parts:
                    contents.append(genai_types.Content(role="model", parts=parts))  # type: ignore[arg-type]
            case ToolMessage():
                parts = []
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        # Gemini's response uses "output" for success and "error"
                        # for failure — the "result"/absent-error shape is
                        # rejected by newer models (e.g. Gemini 3 Flash Preview).
                        # functionResponse also correlates to its functionCall by
                        # tool *name*, not the per-call id.
                        response_key = "error" if content.is_error else "output"
                        parts.append(
                            genai_types.Part(
                                function_response=genai_types.FunctionResponse(
                                    name=content.tool_name or content.id,
                                    response={response_key: tool_result_text(content)},
                                    parts=gemini_function_response_parts(content),
                                ),
                            )
                        )
                if parts:
                    contents.append(genai_types.Content(role="user", parts=parts))  # type: ignore[arg-type]

    return system, contents


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


class GoogleVertexAPI(BaseAPI):
    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        self._client = _build_client(options)

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
        seen_tool_ids: set[str] = set()
        response_reported = False

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
                            yield ThinkingDeltaEvent(
                                thinking=ThinkingContent(content=part.text)  # type: ignore[arg-type]
                            )
                        elif part.text:
                            if thinking_started:
                                yield ThinkingEndEvent(
                                    thinking=ThinkingContent(  # type: ignore[arg-type]
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
                            call_metadata = (
                                {"thought_signature": _encode_signature(part.thought_signature)}
                                if getattr(part, "thought_signature", None)
                                else {}
                            )
                            yield ToolCallStartEvent(
                                tool_call=ToolCallContent(  # type: ignore[arg-type]
                                    id=tool_id, name=tool_name, metadata=call_metadata
                                )
                            )
                            yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id))  # type: ignore[arg-type]
                            yield ToolCallEndEvent(
                                tool_call=ToolCallContent(  # type: ignore[arg-type]
                                    id=tool_id,
                                    name=tool_name,
                                    args=json.loads(args_str) if args_str else {},
                                    metadata=call_metadata,
                                )
                            )
                            tool_index += 1

                finish_reason = getattr(candidate, "finish_reason", None)
                if finish_reason and str(finish_reason) not in ("", "FINISH_REASON_UNSPECIFIED"):
                    if thinking_started:
                        yield ThinkingEndEvent(
                            thinking=ThinkingContent(  # type: ignore[arg-type]
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
                thinking=ThinkingContent(  # type: ignore[arg-type]
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
