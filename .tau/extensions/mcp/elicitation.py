"""MCP elicitation passthrough: a server asks the user for input mid-call.
Mapped onto Tau's existing dialog primitives (ctx.ui.prompt/select/confirm)
when a TUI is attached; otherwise auto-declined or auto-approved per config."""

from __future__ import annotations

from typing import Any

import mcp.types as types


def make_elicitation_handler(tau, *, auto_approve: bool):
    latest_ctx: dict[str, Any] = {"ctx": None}

    @tau.on("turn_start")
    async def _capture(_event, ctx):
        latest_ctx["ctx"] = ctx

    async def handler(params: types.ElicitRequestParams) -> types.ElicitResult:
        ctx = latest_ctx["ctx"]

        if ctx is None or not ctx.has_ui or ctx.ui is None:
            if auto_approve:
                return types.ElicitResult(action="accept", content={})
            return types.ElicitResult(action="decline")

        if isinstance(params, types.ElicitRequestURLParams):
            approved = await ctx.ui.confirm(
                "MCP server requests to open a URL", f"{params.message}\n\n{params.url}"
            )
            return types.ElicitResult(action="accept" if approved else "decline")

        schema = params.requestedSchema or {}
        properties: dict[str, Any] = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])

        if not properties:
            approved = await ctx.ui.confirm("MCP server requests input", params.message)
            return types.ElicitResult(action="accept" if approved else "decline", content={})

        content: dict[str, Any] = {}
        for key, prop in properties.items():
            label = prop.get("title") or key
            enum_values = prop.get("enum")
            if enum_values:
                choice = await ctx.ui.select(f"{params.message}\n{label}", list(enum_values))
                if choice is None:
                    return types.ElicitResult(action="cancel")
                content[key] = choice
                continue

            value = await ctx.ui.prompt(f"{params.message}\n{label}")
            if value is None:
                return types.ElicitResult(action="cancel")
            if not value and key not in required:
                continue
            content[key] = value

        return types.ElicitResult(action="accept", content=content)

    return handler
