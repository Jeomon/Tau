from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ollama import AsyncClient, ChatResponse

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    parse_tool_args,
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
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingLevel,
    ThinkingStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    normalize_structured_response_format,
)
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

if TYPE_CHECKING:
    from tau.message.types import LLMMessage

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
}


class _HTTPError(Exception):
    """Thin wrapper so classify_error can read the HTTP status code."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(body)
        self.status_code = status_code


def _messages_to_ollama(
    messages: list[LLMMessage], supports_thinking: bool = True
) -> list[dict[str, Any]]:
    """Convert a message list to Ollama Chat API format, placing images in a separate field.

    When supports_thinking is False, ThinkingContent is merged into the text
    content (thinking first, then text) so non-thinking models receive full
    context without a thinking field they cannot use.
    This merge is in-memory only; the session file is not affected.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        match msg:
            case SystemMessage():
                text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
                result.append({"role": "system", "content": text})
            case UserMessage():
                text_parts: list[str] = []
                images: list[str] = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            text_parts.append(item.content)
                        case ImageContent():
                            images.extend(b64 for b64, _ in item.to_base64())
                if not text_parts and not images:
                    continue
                entry: dict[str, Any] = {"role": "user", "content": "\n".join(text_parts)}
                if images:
                    entry["images"] = images
                result.append(entry)
            case AssistantMessage():
                text_parts = []
                thinking_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            text_parts.append(item.content)
                        case ThinkingContent():
                            thinking_parts.append(item.content)
                        case ToolCallContent():
                            tool_calls.append(
                                {"function": {"name": item.name, "arguments": item.args}}
                            )
                if supports_thinking:
                    content = "\n".join(text_parts)
                    entry = {"role": "assistant", "content": content}
                    if thinking_parts:
                        entry["thinking"] = "\n".join(thinking_parts)
                else:
                    # Merge thinking before text so context is preserved.
                    content = "\n".join(thinking_parts + text_parts)
                    entry = {"role": "assistant", "content": content}
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                result.append(entry)
            case ToolMessage():
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        result.append({"role": "tool", "content": tool_result_text(content)})

    return result


def _format(response_format: Any | None) -> dict[str, Any] | None:
    """Extract the raw JSON schema dict for Ollama's format field, or None if unstructured."""
    structured = normalize_structured_response_format(response_format)
    return structured.schema if structured is not None else None


class OllamaChatAPI(BaseAPI):
    """Streaming LLM API adapter for the Ollama Chat endpoint."""

    def __init__(self, options: LLMOptions) -> None:
        """Initialise the Ollama AsyncClient targeting the configured host."""
        super().__init__(options)
        self._client = AsyncClient(
            host=options.base_url,
            headers=options.headers or {},
            timeout=options.timeout.total_seconds(),
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def _stream_chat(self, payload: dict[str, Any]):
        """Stream ChatResponse objects from Ollama's raw HTTP layer.

        Bypasses AsyncClient.chat(): the ollama SDK exposes no per-call
        extra-headers param and no raw-response hook, so this reaches into
        its underlying httpx.AsyncClient directly (`self._client._client`)
        to merge live headers and capture status/headers before the body is
        consumed — mirroring what `AsyncClient._request` does internally.
        """
        raw_client = self._client._client
        # Read live, not at client-construction time: a `before_provider_request`
        # extension hook may have mutated `self.options.headers` in place just
        # before this call.
        if self.options.headers:
            raw_client.headers.update(self.options.headers)

        async with raw_client.stream("POST", "/api/chat", json=payload) as response:
            if self.options.on_response:
                self.options.on_response(
                    APIResponse(response.status_code, dict(response.headers))
                )
            if response.is_error:
                chunks: list[bytes] = []
                remaining = 65_536
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk[:remaining])
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
                raise _HTTPError(response.status_code, b"".join(chunks).decode(errors="replace"))
            line_buffer = bytearray()
            async for chunk in response.aiter_bytes():
                line_buffer.extend(chunk)
                if len(line_buffer) > 1_000_000:
                    raise RuntimeError("Ollama stream line exceeds 1 MiB")
                while (newline := line_buffer.find(b"\n")) >= 0:
                    line = bytes(line_buffer[:newline]).strip()
                    del line_buffer[: newline + 1]
                    if line:
                        yield ChatResponse(**json.loads(line))
            if line_buffer.strip():
                yield ChatResponse(**json.loads(line_buffer))

    def _inference_options(self) -> dict[str, Any]:
        """Build Ollama model-level options dict (temperature, token limit)."""
        opts: dict[str, Any] = {"temperature": self.options.temperature}
        if self.options.max_tokens is not None:
            opts["num_predict"] = self.options.max_tokens
        return opts

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        """Stream LLMEvents from the local Ollama Chat endpoint."""
        ollama_messages = _messages_to_ollama(
            context.messages, supports_thinking=bool(model.thinking)
        )
        if context.system_prompt:
            ollama_messages = [
                {"role": "system", "content": context.system_prompt}
            ] + ollama_messages

        think: bool | None = None
        if self.options.thinking_level is not None:
            think = self.options.thinking_level != ThinkingLevel.Off

        text_started = False
        text_buf = ""
        thinking_started = False
        thinking_buf = ""
        _input_tokens = 0
        _output_tokens = 0
        tool_calls_seen = False

        yield StartEvent()

        try:
            payload: dict[str, Any] = {
                "model": model.id,
                "messages": ollama_messages,
                "stream": True,
                "think": think,
                "options": self._inference_options(),
            }
            response_format = _format(context.response_format)
            if response_format is not None:
                payload["format"] = response_format

            tools = context.tools or None
            if tools:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.schema.model_json_schema(),
                        },
                    }
                    for tool in tools
                ]

            if self.options.on_payload:
                modified = self.options.on_payload(payload)
                if modified is not None:
                    payload = modified

            async for chunk in self._stream_chat(payload):
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                msg = chunk.message

                if msg.thinking:
                    if not thinking_started:
                        yield ThinkingStartEvent(thinking=None)
                        thinking_started = True
                    thinking_buf += msg.thinking
                    yield ThinkingDeltaEvent(thinking=ThinkingContent(content=msg.thinking))

                if msg.content:
                    if not text_started:
                        yield TextStartEvent(text=TextContent(content=""))
                        text_started = True
                    text_buf += msg.content
                    yield TextDeltaEvent(text=TextContent(content=msg.content))

                # Ollama sends all tool calls in the final chunk, not incrementally.
                if msg.tool_calls:
                    tool_calls_seen = True
                    for tc in msg.tool_calls:
                        fn = tc.function
                        args_raw = fn.arguments
                        args = parse_tool_args(args_raw)

                        # Ollama doesn't supply tool-call ids; synthesize one so
                        # the engine can pair each result back to its call (and
                        # keep parallel calls distinct).
                        tc_id = getattr(tc, "id", None) or f"call_{uuid4().hex}"

                        yield ToolCallStartEvent(tool_call=ToolCallContent(id=tc_id, name=fn.name))
                        yield ToolCallEndEvent(
                            tool_call=ToolCallContent(id=tc_id, name=fn.name, args=args)
                        )

                if chunk.done:
                    _input_tokens = getattr(chunk, "prompt_eval_count", 0) or 0
                    _output_tokens = getattr(chunk, "eval_count", 0) or 0
                    if thinking_started:
                        yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                    if text_started:
                        yield TextEndEvent(text=TextContent(content=text_buf))
                    # Ollama reports done_reason="stop" even when the response
                    # contains tool calls, so it never maps to StopReason.ToolCalls
                    # on its own. Override here so the engine dispatches the tools
                    # instead of ending the turn with a dangling, unexecuted call.
                    if tool_calls_seen:
                        stop_reason = StopReason.ToolCalls
                    else:
                        stop_reason = _STOP_REASON.get(chunk.done_reason or "", StopReason.Stop)
                    yield EndEvent(
                        reason=stop_reason, input_tokens=_input_tokens, output_tokens=_output_tokens
                    )

        except Exception:
            # Propagate so TextLLM.stream can classify the error (including the
            # _HTTPError raised above, which carries the status code) and drive
            # its retry/backoff logic; yielding an ErrorEvent here would swallow
            # the classification.
            raise
