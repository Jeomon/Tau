from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.utils import (
    openai_messages_to_chat,
    openai_response_format,
    parse_tool_args,
)
from tau.inference.model.types import Model
from tau.inference.provider.oauth.github_copilot import get_copilot_base_url
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
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from tau.message.types import (
    TextContent,
    ToolCallContent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "content_filter": StopReason.ContentFilter,
}


class GitHubCopilotChatAPI(BaseAPI):
    """Streaming LLM API adapter for the GitHub Copilot Chat endpoint (OpenAI-compatible)."""

    def __init__(self, options: LLMOptions) -> None:
        """Resolve the Copilot base URL and initialise the AsyncOpenAI client
        with Copilot headers.
        """
        super().__init__(options)
        base_url = options.base_url or get_copilot_base_url(options.api_key)
        self._client = AsyncOpenAI(
            api_key=options.api_key or "github-copilot",
            base_url=base_url,
            default_headers={**_COPILOT_HEADERS, **(options.headers or {})},
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(
        self, model: Model, messages: list[dict[str, Any]], tools: list[Tool] | None = None
    ) -> dict[str, Any]:
        """Assemble the Copilot Chat Completions request payload."""
        params: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_completion_tokens"] = self.options.max_tokens

        if tools:
            params["tools"] = [
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
            params["tool_choice"] = "auto"

        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        """Stream LLMEvents from the GitHub Copilot Chat API."""
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

        text_started = False
        text_buf = ""
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
            **params, stream=True, stream_options={"include_usage": True}
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

                if delta.content:
                    if not text_started:
                        yield TextStartEvent(text=TextContent(content=""))
                        text_started = True
                    text_buf += delta.content
                    yield TextDeltaEvent(text=TextContent(content=delta.content))

                if delta.tool_calls:
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
