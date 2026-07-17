from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropicVertex  # pyright: ignore[reportPrivateImportUsage]

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    anthropic_apply_message_cache,
    anthropic_messages_to_list,
    anthropic_output_config,
    anthropic_thinking_params,
    has_tool_history,
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

__all__ = ["AnthropicVertexAPI"]

_STOP_REASON: dict[str, StopReason] = {
    "end_turn": StopReason.Stop,
    "max_tokens": StopReason.Length,
    "tool_use": StopReason.ToolCalls,
    "stop_sequence": StopReason.Stop,
}

_DEFAULT_MAX_TOKENS = 8096


def _build_client(options: LLMOptions) -> AsyncAnthropicVertex:
    extra = options.extra_params or {}

    project_id = (
        extra.get("project")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    region = extra.get("region") or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"

    if not project_id:
        raise ValueError(
            "Anthropic on Vertex AI requires a project ID. "
            "Set GOOGLE_CLOUD_PROJECT or pass project in extra_params."
        )

    return AsyncAnthropicVertex(
        project_id=project_id,
        region=region,
        max_retries=options.max_retries,
        timeout=options.timeout.total_seconds(),
    )


class AnthropicVertexAPI(BaseAPI):
    """Streaming LLM API adapter for Anthropic Claude models via Google Cloud Vertex AI."""

    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        self._client = _build_client(options)

    def _build_params(
        self,
        model: Model,
        system: str | None,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        ephemeral_message_count: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model.id,
            "messages": anthropic_apply_message_cache(messages, skip_tail=ephemeral_message_count),
            "max_tokens": self.options.max_tokens or _DEFAULT_MAX_TOKENS,
        }
        if not model.thinking_suppresses_sampling:
            params["temperature"] = self.options.temperature
        if system:
            params["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        params.update(anthropic_thinking_params(model, self.options))

        if tools:
            tool_defs = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.schema.model_json_schema(),
                }
                for tool in tools
            ]
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = tool_defs
        elif has_tool_history(params["messages"]):
            # Anthropic rejects the request outright if tool_use/tool_result blocks
            # exist anywhere in history but `tools` is absent — even an empty list
            # must be sent explicitly (e.g. after an extension calls
            # set_active_tools([]) mid-conversation).
            params["tools"] = []

        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        system, anthropic_messages = anthropic_messages_to_list(
            context.messages, supports_thinking=bool(model.thinking)
        )
        if context.system_prompt:
            system = context.system_prompt
        params = self._build_params(
            model,
            system,
            anthropic_messages,
            tools=context.tools or None,
            ephemeral_message_count=context.ephemeral_message_count,
        )
        output_config = anthropic_output_config(context.response_format)
        if output_config is not None:
            # Merge rather than overwrite: adaptive-thinking models may have
            # already set output_config.effort in _build_params.
            params.setdefault("output_config", {}).update(output_config)

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        block_types: dict[int, str] = {}
        tool_ids: dict[int, str] = {}
        tool_names: dict[int, str] = {}
        text_bufs: dict[int, str] = {}
        thinking_bufs: dict[int, str] = {}
        signature_bufs: dict[int, str] = {}
        tool_bufs: dict[int, str] = {}
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_tokens = 0
        _cache_write_tokens = 0
        _cache_write_1h_tokens = 0

        yield StartEvent()

        # Read live, not at client-construction time: a `before_provider_request`
        # extension hook may have mutated `self.options.headers` in place just
        # before this call.
        extra_headers = self.options.headers or None

        async with self._client.messages.with_streaming_response.create(
            **params, stream=True, extra_headers=extra_headers
        ) as raw_response:
            if self.options.on_response:
                self.options.on_response(
                    APIResponse(
                        raw_response.http_response.status_code,
                        dict(raw_response.http_response.headers),
                    )
                )
            stream = await raw_response.parse()
            async for event in stream:
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                etype = event.type

                if etype == "content_block_start":
                    idx = getattr(event, "index", 0)
                    block = getattr(event, "content_block", None)
                    if block is None:
                        continue
                    btype_start = getattr(block, "type", "")
                    block_types[idx] = btype_start
                    if btype_start == "text":
                        text_bufs[idx] = ""
                        yield TextStartEvent(text=TextContent(content=""))
                    elif btype_start == "thinking":
                        thinking_bufs[idx] = ""
                        yield ThinkingStartEvent(thinking=None)
                    elif btype_start == "tool_use":
                        tool_ids[idx] = getattr(block, "id", "")
                        tool_names[idx] = getattr(block, "name", "")
                        tool_bufs[idx] = ""
                        yield ToolCallStartEvent(
                            tool_call=ToolCallContent(id=tool_ids[idx], name=tool_names[idx])
                        )

                elif etype == "content_block_delta":
                    idx = getattr(event, "index", 0)
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta":
                        text = getattr(delta, "text", "")
                        text_bufs[idx] = text_bufs.get(idx, "") + text
                        yield TextDeltaEvent(text=TextContent(content=text))
                    elif dtype == "thinking_delta":
                        thinking = getattr(delta, "thinking", "")
                        thinking_bufs[idx] = thinking_bufs.get(idx, "") + thinking
                        yield ThinkingDeltaEvent(thinking=ThinkingContent(content=thinking))
                    elif dtype == "signature_delta":
                        signature = getattr(delta, "signature", "")
                        signature_bufs[idx] = signature_bufs.get(idx, "") + signature
                    elif dtype == "input_json_delta":
                        partial = getattr(delta, "partial_json", "")
                        tool_bufs[idx] = tool_bufs.get(idx, "") + partial
                        yield ToolCallDeltaEvent(
                            tool_call=ToolCallContent(id=tool_ids.get(idx, ""))
                        )

                elif etype == "content_block_stop":
                    idx = getattr(event, "index", 0)
                    btype = block_types.get(idx, "")
                    if btype == "text":
                        yield TextEndEvent(text=TextContent(content=text_bufs.get(idx, "")))
                    elif btype == "thinking":
                        yield ThinkingEndEvent(
                            thinking=ThinkingContent(
                                content=thinking_bufs.get(idx, ""),
                                signature=signature_bufs.get(idx, ""),
                            )
                        )
                    elif btype == "tool_use":
                        args_str = tool_bufs.get(idx, "").strip()
                        args = parse_tool_args(args_str)
                        yield ToolCallEndEvent(
                            tool_call=ToolCallContent(
                                id=tool_ids.get(idx, ""), name=tool_names.get(idx, ""), args=args
                            )
                        )

                elif etype == "message_start":
                    u = getattr(getattr(event, "message", None), "usage", None)
                    if u:
                        _input_tokens = getattr(u, "input_tokens", 0) or 0
                        _cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
                        _cache_write_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0
                        _cc = getattr(u, "cache_creation", None)
                        _cache_write_1h_tokens = getattr(_cc, "ephemeral_1h_input_tokens", 0) or 0

                elif etype == "message_delta":
                    u = getattr(event, "usage", None)
                    if u:
                        _output_tokens = getattr(u, "output_tokens", 0) or 0
                    delta = getattr(event, "delta", None)
                    raw_stop = getattr(delta, "stop_reason", None) or ""
                    if raw_stop == "refusal":
                        from tau.inference.utils import ErrorKind

                        stop_details = getattr(delta, "stop_details", None)
                        explanation = (
                            getattr(stop_details, "explanation", None)
                            or "The model refused to complete the request."
                        )
                        yield ErrorEvent(
                            reason=StopReason.Error,
                            error=explanation,
                            kind=ErrorKind.CONTENT_BLOCKED,
                        )
                    else:
                        stop_reason = _STOP_REASON.get(raw_stop, StopReason.Stop)
                        yield EndEvent(
                            reason=stop_reason,
                            input_tokens=_input_tokens,
                            output_tokens=_output_tokens,
                            cache_read_tokens=_cache_read_tokens,
                            cache_write_tokens=_cache_write_tokens,
                            cache_write_1h_tokens=_cache_write_1h_tokens,
                        )

                elif etype == "error":
                    from tau.inference.utils import classify_error

                    err_obj = getattr(event, "error", None)
                    err_msg = str(getattr(err_obj, "message", None) or event)
                    classified = classify_error(ValueError(err_msg))
                    yield ErrorEvent(reason=StopReason.Error, error=err_msg, kind=classified.kind)
