from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.types import APIResponse
from tau.inference.api.text.utils import (
    openai_messages_to_chat,
    openai_response_format,
    stream_openai_chat_events,
)
from tau.inference.model.types import Model
from tau.inference.provider.oauth.github_copilot import get_copilot_base_url
from tau.inference.types import (
    LLMContext,
    LLMEvent,
    LLMOptions,
    StartEvent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
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

    async def aclose(self) -> None:
        await self._client.close()

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
        # Copilot tokens expire (~30 min) and TextLLM.stream refreshes them by
        # assigning self.options.api_key — re-sync the client's bearer before
        # each request so it never keeps sending a stale token (same pattern
        # as openai_completions.py).
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
            ):
                yield event
