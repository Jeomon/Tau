from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

import httpx
import websockets
import websockets.asyncio.client

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
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
    Transport,
    normalize_structured_response_format,
)
from tau.message.types import (
    AssistantMessage,
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
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
_JWT_CLAIM_PATH = "https://api.openai.com/auth"
_MAX_RETRIES = 3
_BASE_DELAY_S = 1.0
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRYABLE_RE = re.compile(
    r"rate.?limit|overloaded|service.?unavailable|upstream.?connect|connection.?refused", re.I
)
_COMPLETION_TYPES = {"response.done", "response.completed", "response.incomplete"}

# Codex 0.144 marks these as "Responses Lite" models: the backend rejects the
# legacy Responses envelope for them (HTTP 404 "Model not found") and expects
# tools/instructions folded into `input`, forced tool_choice/parallel_tool_calls,
# a prompt_cache_key tied to a UUIDv7 session id, and reasoning.context set.

_RESPONSES_LITE_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
_CODEX_COMPATIBILITY_VERSION = "0.144.0"
_RESPONSES_LITE_HEADER = "x-openai-internal-codex-responses-lite"
_RESPONSES_LITE_CLIENT_METADATA = "ws_request_header_x_openai_internal_codex_responses_lite"


def _uuid7() -> str:
    """Generate a UUIDv7 (RFC 9562): 48-bit ms timestamp + random tail.

    The Codex Responses Lite backend keys session affinity off this id, so it
    must look like a real UUIDv7 (version/variant nibbles set correctly).
    """
    import os
    import time

    unix_ts_ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    b[0:6] = unix_ts_ms.to_bytes(6, "big")
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9:16] = rand[3:10]
    return str(uuid.UUID(bytes=bytes(b)))


_THINKING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Low: "low",
    ThinkingLevel.Minimal: "low",
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


# ── Auth ──────────────────────────────────────────────────────────────────────


def _extract_account_id(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a JWT")
        padding = (4 - len(parts[1]) % 4) % 4
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * padding))
        account_id = payload.get(_JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
        if not account_id:
            raise ValueError("missing chatgpt_account_id")
        return account_id
    except Exception as exc:
        raise ValueError(f"Failed to extract account_id from token: {exc}") from exc


# ── URL ───────────────────────────────────────────────────────────────────────


def _resolve_http_url(base_url: str | None) -> str:
    raw = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}/codex/responses"


def _resolve_ws_url(base_url: str | None) -> str:
    http = _resolve_http_url(base_url)
    return http.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


# ── Message → input conversion ────────────────────────────────────────────────


def _content_to_input(content_items: list, role: str) -> list[dict[str, Any]]:
    # The Responses API requires assistant text parts to be "output_text"
    # ("input_text" is only valid for user/input content).
    text_type = "output_text" if role == "assistant" else "input_text"
    parts: list[dict[str, Any]] = []
    for item in content_items:
        match item:
            case TextContent():
                parts.append({"type": text_type, "text": item.content})
            case ImageContent():
                for b64, mime in item.to_base64():
                    url = (
                        b64
                        if b64.startswith("http")
                        else f"data:{mime or 'image/png'};base64,{b64}"
                    )
                    parts.append({"type": "input_image", "image_url": url})
            case FileContent():
                for b64, mime in item.to_base64():
                    parts.append({"type": "input_file", "file_data": f"data:{mime};base64,{b64}"})
    return parts


def _messages_to_input(messages: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
    instructions = "You are a helpful assistant."
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        match msg:
            case SystemMessage():
                text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
                if text:
                    instructions = text
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
                parts = _content_to_input(msg.contents, "user")
                if parts:
                    input_items.append({"role": "user", "content": parts})
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
                            # (store: False) — the signature is the full raw
                            # reasoning item (including encrypted_content) captured
                            # at stream time. Drop unsigned blocks (older sessions,
                            # or left over from a provider/model switch) instead of
                            # sending a malformed reasoning item.
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
                            text_parts.append({"type": "output_text", "text": content.content})
                        case ImageContent():
                            for b64, mime in content.to_base64():
                                url = (
                                    b64
                                    if b64.startswith("http")
                                    else f"data:{mime or 'image/png'};base64,{b64}"
                                )
                                text_parts.append({"type": "input_image", "image_url": url})
                        case FileContent():
                            for b64, mime in content.to_base64():
                                text_parts.append(
                                    {"type": "input_file", "file_data": f"data:{mime};base64,{b64}"}
                                )
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


# ── Request building ──────────────────────────────────────────────────────────


def _build_body(
    model: Model,
    instructions: str,
    input_items: list[dict[str, Any]],
    options: LLMOptions,
    tools: list[Tool] | None = None,
) -> dict[str, Any]:
    effort = (
        _THINKING_EFFORT.get(options.thinking_level, "medium")
        if options.thinking_level
        else "medium"
    )
    body: dict[str, Any] = {
        "model": model.id,
        "store": False,
        "stream": True,
        "instructions": instructions,
        "input": input_items,
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": effort, "summary": "auto"},
    }
    # NOTE: the ChatGPT Codex backend rejects `max_output_tokens` and
    # `prompt_cache_options` ("Unsupported parameter") — unlike the standard
    # OpenAI Responses API. Output length is governed by the subscription, and
    # caching is handled server-side by the Codex backend itself, so neither
    # is ever sent here. See openai_responses.py for the direct-API path,
    # where prompt_cache_options is genuinely supported.

    if tools:
        body["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.schema.model_json_schema(),
            }
            for tool in tools
        ]
    return body


def _apply_responses_lite(body: dict[str, Any], session_id: str) -> dict[str, Any]:
    tools = body.pop("tools", None)
    instructions = body.pop("instructions", None)

    prefix: list[dict[str, Any]] = [
        {"type": "additional_tools", "role": "developer", "tools": tools or []}
    ]
    if instructions:
        prefix.append(
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": instructions}],
            }
        )
    body["input"] = [*prefix, *body.get("input", [])]
    body["tool_choice"] = "auto"
    body["parallel_tool_calls"] = False
    body["prompt_cache_key"] = session_id
    body["reasoning"] = {**(body.get("reasoning") or {}), "context": "all_turns"}
    return body


def _build_headers(
    token: str, account_id: str, session_id: str, *, websocket: bool = False, lite: bool = False
) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "originator": "codex_cli_rs",
        # Without a session identity the backend routes the request through an
        # internal A/B experiment slug (e.g. "<model>-free-1p-codexswic-ev3")
        # that doesn't resolve, and reports the base model as "not found".
        "session-id": session_id,
        "x-client-request-id": session_id,
    }
    if lite:
        headers["version"] = _CODEX_COMPATIBILITY_VERSION
        headers["x-session-affinity"] = session_id
    if websocket:
        headers["OpenAI-Beta"] = "responses_websockets=2026-02-06"
        if lite:
            headers[_RESPONSES_LITE_HEADER] = "true"
    else:
        headers["OpenAI-Beta"] = "responses=experimental"
        headers["accept"] = "text/event-stream"
        headers["content-type"] = "application/json"
    return headers


# ── Codex event normalization ─────────────────────────────────────────────────


async def _map_codex_events(
    raw: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    async for event in raw:
        etype = event.get("type", "")

        if etype == "error":
            code = event.get("code", "")
            message = event.get("message", "") or code or json.dumps(event)
            raise RuntimeError(f"Codex error: {message}")

        if etype == "response.failed":
            err = (event.get("response") or {}).get("error") or {}
            raise RuntimeError(err.get("message") or "Codex response failed")

        if etype in _COMPLETION_TYPES:
            yield {**event, "type": "response.completed", "response": event.get("response") or {}}
            return

        yield event


# ── SSE parsing ───────────────────────────────────────────────────────────────


async def _parse_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            data_lines = [
                line[5:].strip() for line in block.splitlines() if line.startswith("data:")
            ]
            if not data_lines:
                continue
            data = "\n".join(data_lines).strip()
            if not data or data == "[DONE]":
                continue
            yield json.loads(data)


# ── WebSocket parsing ─────────────────────────────────────────────────────────


async def _parse_ws(
    ws: websockets.asyncio.client.ClientConnection,
) -> AsyncIterator[dict[str, Any]]:
    saw_completion = False
    async for raw in ws:
        event: dict[str, Any] = json.loads(raw)
        etype = event.get("type", "")
        if etype in _COMPLETION_TYPES:
            saw_completion = True
        yield event
        if saw_completion:
            return
    if not saw_completion:
        raise RuntimeError("WebSocket stream closed before completion event")


# ── Retry helper ──────────────────────────────────────────────────────────────


def _is_retryable(status: int, body: str) -> bool:
    return status in _RETRYABLE_STATUSES or bool(_RETRYABLE_RE.search(body))


# ── Event processing ──────────────────────────────────────────────────────────


async def _process_events(events: AsyncIterator[dict[str, Any]]) -> AsyncGenerator[LLMEvent, None]:
    # The Responses API gives a function_call output item both an `id`
    # (e.g. "fc_...") and a `call_id` (e.g. "call_..."). The subsequent
    # function_call_arguments.delta/.done events reference the item by
    # `item_id` (== item `id`), while the tool result must be paired by
    # `call_id`. Map item id -> (call_id, name) to bridge the two.
    call_id_by_item: dict[str, str] = {}
    name_by_item: dict[str, str] = {}
    # The Codex backend delivers a complete function_call as a single
    # response.output_item.done (no streamed *.arguments.delta/.done events),
    # while the standard streaming path emits them incrementally. Track which
    # call_ids have already been started/ended so the two paths don't duplicate.
    started_calls: set[str] = set()
    ended_calls: set[str] = set()
    # Final reasoning summary text, keyed by item id, buffered until
    # response.output_item.done delivers the full item (id + encrypted_content)
    # so ThinkingEndEvent can carry both in one shot.
    reasoning_text_by_item: dict[str, str] = {}
    saw_tool_call = False
    _input_tokens = 0
    _output_tokens = 0
    _cache_read_tokens = 0
    _cache_write_tokens = 0

    async for event in events:
        etype = event.get("type", "")

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            itype = item.get("type", "")
            if itype == "message":
                yield TextStartEvent(text=TextContent(content=""))  # type: ignore[arg-type]
            elif itype == "reasoning":
                yield ThinkingStartEvent(thinking=None)
            elif itype == "function_call":
                item_id = item.get("id", "")
                call_id = item.get("call_id", "")
                name = item.get("name", "")
                saw_tool_call = True
                call_id_by_item[item_id] = call_id
                name_by_item[item_id] = name
                started_calls.add(call_id)
                yield ToolCallStartEvent(tool_call=ToolCallContent(id=call_id, name=name))  # type: ignore[arg-type]

        elif etype == "response.output_text.delta":
            yield TextDeltaEvent(text=TextContent(content=event.get("delta", "")))  # type: ignore[arg-type]

        elif etype == "response.output_text.done":
            yield TextEndEvent(text=TextContent(content=event.get("text", "")))  # type: ignore[arg-type]

        elif etype == "response.reasoning_summary_text.delta":
            yield ThinkingDeltaEvent(thinking=ThinkingContent(content=event.get("delta", "")))  # type: ignore[arg-type]

        elif etype == "response.reasoning_summary_text.done":
            reasoning_text_by_item[event.get("item_id", "")] = event.get("text", "")

        elif etype == "response.function_call_arguments.delta":
            item_id = event.get("item_id", "")
            yield ToolCallDeltaEvent(
                tool_call=ToolCallContent(id=call_id_by_item.get(item_id, item_id))  # type: ignore[arg-type]
            )

        elif etype == "response.function_call_arguments.done":
            item_id = event.get("item_id", "")
            args_str = event.get("arguments", "").strip()
            args = parse_tool_args(args_str)

            call_id = call_id_by_item.get(item_id, item_id)
            saw_tool_call = True
            ended_calls.add(call_id)  # type: ignore[arg-type]
            yield ToolCallEndEvent(
                tool_call=ToolCallContent(id=call_id, name=name_by_item.get(item_id, ""), args=args)  # type: ignore[arg-type]
            )

        elif etype == "response.output_item.done":
            item = event.get("item") or {}
            itype = item.get("type", "")
            if itype == "function_call":
                call_id = item.get("call_id", "")
                name = item.get("name", "")
                saw_tool_call = True
                if call_id not in ended_calls:
                    args_str = (item.get("arguments") or "").strip()
                    args = parse_tool_args(args_str)
                    if call_id not in started_calls:
                        started_calls.add(call_id)
                        yield ToolCallStartEvent(tool_call=ToolCallContent(id=call_id, name=name))  # type: ignore[arg-type]
                    ended_calls.add(call_id)
                    yield ToolCallEndEvent(
                        tool_call=ToolCallContent(id=call_id, name=name, args=args)  # type: ignore[arg-type]
                    )
            elif itype == "reasoning":
                # The full item (id, summary, encrypted_content when requested via
                # `include: ["reasoning.encrypted_content"]`) is only available here,
                # not on the earlier reasoning_summary_text.delta/.done events — so
                # the signature (needed to replay this reasoning item statelessly on
                # the next turn, since store: False) is captured now.
                content = reasoning_text_by_item.pop(item.get("id", ""), "")
                yield ThinkingEndEvent(
                    thinking=ThinkingContent(content=content, signature=json.dumps(item))  # type: ignore[arg-type]
                )

        elif etype == "response.completed":
            response = event.get("response") or {}
            usage = response.get("usage") or {}
            _input_tokens = usage.get("input_tokens", 0) or 0
            _output_tokens = usage.get("output_tokens", 0) or 0
            _details = usage.get("input_tokens_details") or {}
            _cache_read_tokens = _details.get("cached_tokens", 0) or 0
            _cache_write_tokens = _details.get("cache_write_tokens", 0) or 0
            stop_reason = _STOP_REASON.get(response.get("stop_reason") or "", StopReason.Stop)
            if saw_tool_call and stop_reason == StopReason.Stop:
                stop_reason = StopReason.ToolCalls
            yield EndEvent(
                reason=stop_reason,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
                cache_read_tokens=_cache_read_tokens,
                cache_write_tokens=_cache_write_tokens,
                input_tokens_include_cache_read=True,
            )


# ── API class ─────────────────────────────────────────────────────────────────


class OpenAICodexResponsesAPI(BaseAPI):
    SUPPORTED_TRANSPORTS = (Transport.HTTP, Transport.WEBSOCKET)

    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        self._http_url = _resolve_http_url(options.base_url)
        self._ws_url = _resolve_ws_url(options.base_url)
        self._session_id = str(uuid.uuid4())
        # Responses Lite ties session affinity to a UUIDv7; generated lazily
        # (only Lite models need it) and reused for the life of this adapter.
        self._lite_session_id: str | None = None

    async def _stream_sse(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncGenerator[LLMEvent, None]:
        body_bytes = json.dumps(body).encode()
        last_error: Exception | None = None

        from tau.utils.ssl_context import get_shared_ssl_context

        # Per-call client in an async-with so the connection pool is always
        # closed when the stream ends or is torn down — no persistent client
        # left unclosed for the GC to warn about.
        async with httpx.AsyncClient(
            timeout=self.options.timeout.total_seconds(),
            headers=self.options.headers or {},
            verify=get_shared_ssl_context(),
        ) as client:
            for attempt in range(_MAX_RETRIES + 1):
                if attempt > 0:
                    await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
                try:
                    async with client.stream(
                        "POST",
                        self._http_url,
                        content=body_bytes,
                        headers=headers,
                    ) as response:
                        if self.options.on_response:
                            self.options.on_response(
                                APIResponse(response.status_code, dict(response.headers))
                            )

                        if not response.is_success:
                            text = (await response.aread()).decode(errors="replace")
                            if attempt < _MAX_RETRIES and _is_retryable(response.status_code, text):
                                last_error = RuntimeError(f"HTTP {response.status_code}: {text}")
                                continue
                            raise RuntimeError(f"HTTP {response.status_code}: {text}")

                        async for event in _process_events(_map_codex_events(_parse_sse(response))):
                            yield event
                        return

                except RuntimeError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < _MAX_RETRIES:
                        continue
                    raise

        raise last_error or RuntimeError("Failed after retries")

    async def _stream_ws(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
        *,
        lite: bool = False,
    ) -> AsyncGenerator[LLMEvent, None]:
        ws_headers = {
            k: v for k, v in headers.items() if k.lower() not in ("accept", "content-type")
        }
        if lite:
            body = {
                **body,
                "client_metadata": {
                    **(body.get("client_metadata") or {}),
                    _RESPONSES_LITE_CLIENT_METADATA: "true",
                },
            }
        async with websockets.asyncio.client.connect(
            self._ws_url,
            additional_headers=ws_headers,
        ) as ws:
            await ws.send(json.dumps({"type": "response.create", **body}))
            async for event in _process_events(_map_codex_events(_parse_ws(ws))):
                yield event

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        token = self.options.api_key or ""
        account_id = _extract_account_id(token)
        instructions, input_items = _messages_to_input(context.messages)
        body = _build_body(
            model, instructions, input_items, self.options, tools=context.tools or None
        )
        text_format = _text_format(context.response_format)
        if text_format is not None:
            body["text"] = {**body.get("text", {}), **text_format}

        lite = model.id in _RESPONSES_LITE_MODELS
        session_id = self._session_id
        if lite:
            if self._lite_session_id is None:
                self._lite_session_id = _uuid7()
            session_id = self._lite_session_id
            body = _apply_responses_lite(body, session_id)

        if self.options.on_payload:
            modified = self.options.on_payload(body)
            if modified is not None:
                body = modified

        yield StartEvent()

        if self.options.transport == Transport.WEBSOCKET:
            headers = _build_headers(token, account_id, session_id, websocket=True, lite=lite)
            stream_iter = self._stream_ws(body, headers, lite=lite)
        else:
            headers = _build_headers(token, account_id, session_id, websocket=False, lite=lite)
            stream_iter = self._stream_sse(body, headers)

        cancelled = False
        # aclosing() so breaking out on cancellation deterministically tears
        # down the inner SSE/WS stream (and its httpx/websocket connection)
        # here, instead of leaving it to the GC asyncgen finalizer
        # ("Task was destroyed but it is pending!").
        async with aclosing(stream_iter) as stream:
            async for event in stream:
                if self._cancelled():
                    cancelled = True
                    break
                yield event
        if cancelled:
            yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
