from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class BeforeProviderRequestEvent:
    """Fired just before the LLM API call is made.

    ``headers`` is the same dict object the provider's HTTP client sends on
    this request (passed as ``extra_headers`` at call time rather than baked
    in at client construction), so mutating it in place — e.g. to add a
    tracing header — takes effect on the imminent request.
    """

    type: Literal["before_provider_request"] = field(default="before_provider_request", init=False)
    model: Any = None
    provider_id: str | None = None
    messages: list[Any] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    options: Any = None


@dataclass
class AfterProviderResponseEvent:
    """Fired immediately after the LLM streaming response is fully collected.

    ``status_code``/``response_headers`` carry the raw HTTP response info
    (captured as soon as headers arrived, before the stream body was
    consumed) for providers that report it — currently Anthropic Messages
    and the OpenAI Completions/Responses APIs. ``None`` for providers that
    don't yet report it.
    """

    type: Literal["after_provider_response"] = field(default="after_provider_response", init=False)
    model: Any = None
    response: Any = None
    status_code: int | None = None
    response_headers: dict[str, str] | None = None
