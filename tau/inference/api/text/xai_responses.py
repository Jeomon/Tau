"""
xAI Grok CLI API — thin wrapper around the OpenAI Responses API adapter,
pointed at the Grok CLI's cli-chat-proxy.grok.com backend.

That proxy gatekeeps on client-identity headers (confirmed live: a request
without ``x-grok-client-version`` is rejected with HTTP 426 "outdated
version"), which the plain OpenAI Responses client doesn't send.
"""

from __future__ import annotations

from tau.inference.api.text.openai_responses import OpenAIResponsesAPI
from tau.inference.types import LLMOptions

__all__ = ["XAIAPIResponses"]

_DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
_CLIENT_VERSION = "0.2.93"

_GROK_CLI_HEADERS = {
    "x-grok-client-identifier": "grok-cli",
    "x-grok-client-version": _CLIENT_VERSION,
    "x-xai-token-auth": "xai-grok-cli",
}


class XAIAPIResponses(OpenAIResponsesAPI):
    def __init__(self, options: LLMOptions) -> None:
        options.base_url = options.base_url or _DEFAULT_BASE_URL
        options.headers = {**_GROK_CLI_HEADERS, **(options.headers or {})}
        super().__init__(options)
