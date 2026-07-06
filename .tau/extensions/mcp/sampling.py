"""MCP sampling/createMessage passthrough: an MCP server asks to run a model
completion through the *active Tau session's* LLM instead of bringing its own
API key."""

from __future__ import annotations

from typing import Any

import mcp.types as types


def make_sampling_handler(tau, *, auto_approve: bool):
    """Build the SamplingHandler used by every McpServerHandle.

    Captures the most-recently-seen live ExtensionContext via `turn_start` /
    `agent_end`, since the callback fires from server-driven code outside the
    normal command/event dispatch path and needs a stashed live reference.
    """
    latest_ctx: dict[str, Any] = {"ctx": None}

    @tau.on("turn_start")
    async def _capture(_event, ctx):
        latest_ctx["ctx"] = ctx

    @tau.on("agent_end")
    async def _capture_end(_event, ctx):
        latest_ctx["ctx"] = ctx

    async def handler(params: types.CreateMessageRequestParams) -> types.CreateMessageResult:
        if not auto_approve:
            raise PermissionError("sampling auto-approve is disabled in extension settings")

        ctx = latest_ctx["ctx"]
        if ctx is None or ctx.llm is None:
            raise RuntimeError("no active session LLM available for sampling")

        from tau.inference.types import LLMContext, TextDeltaEvent, TextEndEvent
        from tau.message.types import AssistantMessage, UserMessage

        messages = []
        for m in params.messages:
            text = m.content.text if isinstance(m.content, types.TextContent) else str(m.content)
            if m.role == "assistant":
                messages.append(AssistantMessage.from_text(text))
            else:
                messages.append(UserMessage.from_text(text))

        llm_context = LLMContext(messages=messages, system_prompt=params.systemPrompt)
        events = await ctx.llm.invoke(llm_context)

        text = ""
        for e in events:
            if isinstance(e, (TextEndEvent, TextDeltaEvent)):
                text = e.text.content

        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text=text),
            model=ctx.model_id,
            stopReason="endTurn",
        )

    return handler
