from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from tau.inference.api.text import dialect
from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    openai_messages_to_chat,
    openai_response_format,
    stream_openai_chat_events,
)
from tau.inference.model.types import Model
from tau.inference.types import (
    LLMContext,
    LLMEvent,
    LLMOptions,
    StartEvent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool


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

    async def aclose(self) -> None:
        await self._client.close()

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

        yield StartEvent()

        # Read live, not at client-construction time: a `before_provider_request`
        # extension hook may have mutated `self.options.headers` in place just
        # before this call.
        extra_headers = self.options.headers or None

        # async with closes the SDK stream (and its httpx response) on every
        # exit path — cancellation return or an upstream GeneratorExit — instead
        # of leaving it to the GC asyncgen finalizer.
        async with self._client.chat.completions.with_streaming_response.create(
            **params,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
            extra_headers=extra_headers,
        ) as raw_response:
            if self.options.on_response:
                self.options.on_response(
                    APIResponse(
                        raw_response.http_response.status_code,
                        dict(raw_response.http_response.headers),
                    )
                )
            sdk_stream = await raw_response.parse()
            async for event in stream_openai_chat_events(
                sdk_stream,
                cancelled=self._cancelled,
                extract_thinking=lambda d: dialect.extract_thinking_delta(d),
            ):
                yield event
