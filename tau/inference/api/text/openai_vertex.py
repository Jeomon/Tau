from __future__ import annotations

import os
import time
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
from tau.inference.types import (
    LLMContext,
    LLMEvent,
    LLMOptions,
    StartEvent,
)

if TYPE_CHECKING:
    from tau.tool.types import Tool

__all__ = ["OpenAIVertexAPI"]

# GCP access tokens expire after 1 hour; refresh 5 minutes early
_TOKEN_REFRESH_BUFFER = 300


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
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


class _TokenCache:
    """Caches a GCP access token and refreshes it before expiry."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get(self) -> str:
        if self._token is None or time.time() >= self._expires_at:
            self._refresh()
        return self._token  # type: ignore[return-value]

    def _refresh(self) -> None:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        self._token = credentials.token
        # google.auth sets expiry as a datetime; fall back to 1h if absent
        if credentials.expiry is not None:
            self._expires_at = credentials.expiry.timestamp() - _TOKEN_REFRESH_BUFFER
        else:
            self._expires_at = time.time() + 3600 - _TOKEN_REFRESH_BUFFER


# Module-level cache so tokens are shared across requests
_token_cache = _TokenCache()


def _base_url(model: Model, project: str, location: str) -> str:
    # model.base_url holds the publisher path, e.g.
    # "publishers/meta/models/llama-4-maverick-17b-128e-instruct-maas"
    publisher_path = model.base_url or f"publishers/openai/models/{model.id}"
    return (
        f"https://{location}-aiplatform.googleapis.com"
        f"/v1/projects/{project}/locations/{location}/{publisher_path}"
    )


def _vertex_thinking_delta(delta: Any) -> str | None:
    """Vertex's OpenAI endpoint reports reasoning under either name, by model."""
    return getattr(delta, "reasoning_content", None) or getattr(delta, "thinking", None)


class OpenAIVertexAPI(BaseAPI):
    """Streaming adapter for OpenAI-compatible models on Vertex AI (Llama, Mistral, Grok).

    Auth is handled via GCP Application Default Credentials — run
    `gcloud auth application-default login` before use.
    """

    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        extra = options.extra_params or {}
        self._project = (
            extra.get("project")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
        )
        self._location = (
            extra.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"
        )
        if not self._project:
            raise ValueError(
                "Vertex AI requires a project ID. "
                "Set GOOGLE_CLOUD_PROJECT or pass project in extra_params."
            )

    def _make_client(self, model: Model) -> AsyncOpenAI:
        token = _token_cache.get()
        return AsyncOpenAI(
            api_key=token,
            base_url=_base_url(model, self._project, self._location),  # type: ignore[arg-type]
            timeout=self.options.timeout.total_seconds(),
            max_retries=0,  # retry logic lives in TextLLM.stream()
        )

    def _build_params(
        self, model: Model, messages: list[dict[str, Any]], tools: list[Tool] | None = None
    ) -> dict[str, Any]:
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
                        "parameters": _clean_schema(tool.schema.model_json_schema()),
                    },
                }
                for tool in tools
            ]
            params["tool_choice"] = "auto"

        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        client = self._make_client(model)
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

        async with client.chat.completions.with_streaming_response.create(
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
                extract_thinking=_vertex_thinking_delta,
            ):
                yield event
