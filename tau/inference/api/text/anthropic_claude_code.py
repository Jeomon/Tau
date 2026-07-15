from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic

from tau.inference.api.text.base import BaseLLMAPI as BaseAPI
from tau.inference.api.text.utils import (
    anthropic_apply_message_cache,
    anthropic_messages_to_list,
    anthropic_output_config,
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
    ThinkingBudgets,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingLevel,
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

_STOP_REASON: dict[str, StopReason] = {
    "end_turn": StopReason.Stop,
    "max_tokens": StopReason.Length,
    "tool_use": StopReason.ToolCalls,
    "stop_sequence": StopReason.Stop,
}

_DEFAULT_MAX_TOKENS = 8096


_OAUTH_HEADERS = {
    "x-app": "cli",
    "User-Agent": "claude-cli/2.1.122 (external, sdk-cli)",
}

# System identity that Anthropic's API requires as its own system[] entry for
# OAuth-authenticated (Claude Pro/Max) requests to be accepted and billed
# against the subscription rather than rejected with a 400.
_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

_CC_VERSION = "2.1.185"
_BILLING_SALT = "59cf53e54c78"

# Beta flags Claude Code itself sends on every OAuth request.
_BASE_BETAS = [
    "claude-code-20250219",
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "prompt-caching-scope-2026-01-05",
    "context-management-2025-06-27",
    "advisor-tool-2026-03-01",
    "thinking-token-count-2026-05-13",
    "extended-cache-ttl-2025-04-11",
    "effort-2025-11-24",
]

# Per-model beta overrides, matched by substring against the lowercased model id.
_MODEL_BETA_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "haiku": {"exclude": ["interleaved-thinking-2025-05-14"]},
    "4-6": {"add": ["effort-2025-11-24"]},
    "4-7": {"add": ["effort-2025-11-24"]},
}


def _model_betas(model_id: str) -> list[str]:
    """Return the anthropic-beta flags for `model_id`, applying per-model overrides."""
    betas = list(_BASE_BETAS)
    lower = model_id.lower()
    for pattern, override in _MODEL_BETA_OVERRIDES.items():
        if pattern in lower:
            exclude = override.get("exclude", [])
            betas = [b for b in betas if b not in exclude]
            for b in override.get("add", []):
                if b not in betas:
                    betas.append(b)
            break
    return betas


def _first_user_message_text(messages: list[dict[str, Any]]) -> str:
    """Extract the text of the first user message's first text block."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""
    return ""


def _billing_header_value(messages: list[dict[str, Any]], entrypoint: str) -> str:
    """Build the `x-anthropic-billing-header` value Claude Code embeds as system[0].

    Mirrors Claude Code's internal cch/version-suffix computation so OAuth
    requests are billed against the subscription instead of being rejected.
    """
    text = _first_user_message_text(messages)
    version = os.environ.get("ANTHROPIC_CLI_VERSION", _CC_VERSION)
    sampled = "".join(text[i] if i < len(text) else "0" for i in (4, 7, 20))
    suffix = hashlib.sha256(f"{_BILLING_SALT}{sampled}{version}".encode()).hexdigest()[:3]
    cch = hashlib.sha256(text.encode()).hexdigest()[:5]
    return (
        f"x-anthropic-billing-header: cc_version={version}.{suffix}; "
        f"cc_entrypoint={entrypoint}; cch={cch};"
    )


def _build_system_blocks(
    system_text: str | None, messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the system prompt for OAuth requests.

    Anthropic's API validates the system[] array for OAuth-authenticated
    requests: only the billing header and the Claude Code identity string may
    live there. Any other system content triggers a 400 ("out of extra
    usage"). Everything else is relocated to the front of the first user
    message, which is functionally equivalent for the model.

    Returns (system_blocks, patched_messages).
    """
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": _billing_header_value(messages, entrypoint)},
        {"type": "text", "text": _SYSTEM_IDENTITY, "cache_control": {"type": "ephemeral"}},
    ]

    if not system_text:
        return system_blocks, messages

    patched = list(messages)
    prefix = system_text
    for i, message in enumerate(patched):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            patched[i] = {**message, "content": f"{prefix}\n\n{content}"}
        elif isinstance(content, list):
            patched[i] = {
                **message,
                "content": [{"type": "text", "text": prefix}, *content],
            }
        else:
            patched[i] = {**message, "content": prefix}
        break
    return system_blocks, patched


class AnthropicClaudeCodeAPI(BaseAPI):
    """Anthropic Messages API using OAuth token auth (Claude Pro/Max).

    Sends the token via X-Api-Key (not Authorization: Bearer) with the
    required OAuth beta headers, which is what Anthropic's API enforces
    for Claude Max / Pro OAuth tokens.
    """

    def __init__(self, options: LLMOptions) -> None:
        """Initialise the AsyncAnthropic client with OAuth headers merged from options."""
        super().__init__(options)
        merged_headers = {**_OAUTH_HEADERS, **(options.headers or {})}
        self._client = AsyncAnthropic(
            auth_token=options.api_key,  # Bearer auth for OAuth tokens
            base_url=options.base_url,
            default_headers=merged_headers,
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )
        self._current_api_key = options.api_key

    def _build_params(
        self,
        model: Model,
        system: str | None,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        ephemeral_message_count: int = 0,
    ) -> dict[str, Any]:
        """Assemble the Anthropic API request payload, including thinking and tool configs."""
        _suppress_temp = any(s in model.id for s in ("opus-4-7", "opus-4-8"))
        params: dict[str, Any] = {
            "model": model.id,
            "messages": anthropic_apply_message_cache(messages, skip_tail=ephemeral_message_count),
            "max_tokens": self.options.max_tokens or _DEFAULT_MAX_TOKENS,
        }
        if not _suppress_temp:
            params["temperature"] = self.options.temperature
        system_blocks, params["messages"] = _build_system_blocks(system, params["messages"])
        params["system"] = system_blocks
        if (
            self.options.thinking_level is not None
            and self.options.thinking_level != ThinkingLevel.Off
        ):
            budgets = self.options.thinking_budgets or ThinkingBudgets()
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": budgets.get(self.options.thinking_level),
            }

        if tools:
            tool_defs = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.schema.model_json_schema(),
                }
                for tool in tools
            ]
            # Cache the last tool definition to reduce repeated prompt-token charges.
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = tool_defs
        elif has_tool_history(params["messages"]):
            # Anthropic rejects the request outright if tool_use/tool_result blocks
            # exist anywhere in history but `tools` is absent — even an empty list
            # must be sent explicitly (e.g. after an extension calls
            # set_active_tools([]) mid-conversation).
            params["tools"] = []
        return params

    def _sync_client(self) -> None:
        """Rebuild client if the api_key (OAuth token) has been refreshed."""
        if self.options.api_key != self._current_api_key:
            self._current_api_key = self.options.api_key
            merged_headers = {**_OAUTH_HEADERS, **(self.options.headers or {})}
            self._client = AsyncAnthropic(
                auth_token=self.options.api_key,
                base_url=self.options.base_url,
                default_headers=merged_headers,
                max_retries=self.options.max_retries,
                timeout=self.options.timeout.total_seconds(),
            )

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        """Stream LLMEvents from the Anthropic Messages API using an OAuth token."""
        self._sync_client()
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
            params["output_config"] = output_config

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        # Per-block accumulation buffers keyed by content block index.
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

        extra_headers = {"anthropic-beta": ",".join(_model_betas(model.id))}
        async with self._client.messages.stream(**params, extra_headers=extra_headers) as stream:
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
                    yield ErrorEvent(reason=StopReason.Abort, error=str(event))
