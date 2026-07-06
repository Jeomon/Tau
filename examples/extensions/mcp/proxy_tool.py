"""The mcp() proxy tool — one token-cheap entry point to search, describe, and
call tools across every configured MCP server, without registering each one
individually."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from metadata_cache import CachedTool, MetadataCache
from output_guard import guard_text
from pydantic import BaseModel, Field
from server_manager import McpServerError, McpServerManager

from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


class _McpProxySchema(BaseModel):
    search: str | None = Field(
        default=None, description="Fuzzy-search tool names/descriptions across all servers."
    )
    describe: str | None = Field(
        default=None, description="Return the full schema for one tool, e.g. 'server.tool_name'."
    )
    tool: str | None = Field(
        default=None, description="Tool to call, e.g. 'server.tool_name'. Requires `args`."
    )
    args: str | None = Field(
        default=None, description="JSON object string of arguments for `tool`."
    )
    connect: str | None = Field(
        default=None, description="Force-connect a server ahead of time and list its tools."
    )


_DESCRIPTION = """\
Access tools exposed by configured MCP (Model Context Protocol) servers.

Usage:
- mcp() with no arguments — list configured servers and connection state.
- mcp(search="keyword") — fuzzy-search tool names/descriptions across all servers.
- mcp(describe="server.tool_name") — get a tool's full parameter schema.
- mcp(tool="server.tool_name", args='{"key": "value"}') — call a tool.
- mcp(connect="server_name") — connect a server now and list its tools.
"""


def _matches(cached: CachedTool, keyword: str) -> bool:
    k = keyword.lower()
    return k in cached.name.lower() or k in cached.description.lower()


class McpProxyTool(Tool):
    def __init__(
        self,
        manager: McpServerManager,
        cache: MetadataCache,
        temp_dir: Path,
    ) -> None:
        super().__init__(
            name="mcp",
            description=_DESCRIPTION,
            schema=_McpProxySchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._manager = manager
        self._cache = cache
        self._temp_dir = temp_dir

    def _guard_config(self):
        return self._manager.config.settings.output_guard

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params

        try:
            if params.get("tool"):
                return await self._call_tool(invocation)
            if params.get("describe"):
                return self._describe(invocation)
            if params.get("search"):
                return self._search(invocation)
            if params.get("connect"):
                return await self._connect(invocation)
            return self._list_servers(invocation)
        except McpServerError as e:
            return ToolResult.error(invocation.id, str(e))

    def _split(self, ref: str) -> tuple[str, str]:
        if "." not in ref:
            raise McpServerError(
                f"expected 'server.tool_name', got {ref!r}. "
                "Use mcp(search=...) to find the right name."
            )
        server, _, tool = ref.partition(".")
        return server, tool

    def _list_servers(self, invocation: ToolInvocation) -> ToolResult:
        lines = []
        for status in self._manager.status():
            state = "connected" if status.connected else "disconnected"
            err = f" (error: {status.last_error})" if status.last_error else ""
            lines.append(
                f"- {status.name} [{status.lifecycle}] {state}, {status.tool_count} tools{err}"
            )
        if not lines:
            lines = ["No MCP servers configured. Add one to .tau/mcp.json."]
        return ToolResult.ok(invocation.id, "\n".join(lines), metadata={"servers": len(lines)})

    def _search(self, invocation: ToolInvocation) -> ToolResult:
        keyword = invocation.params["search"]
        matches: list[str] = []
        for server, tools in self._cache.all_tools().items():
            for t in tools:
                if _matches(t, keyword):
                    matches.append(f"{server}.{t.name}: {t.description}")
        content = "\n".join(matches) if matches else f"No cached tools matched {keyword!r}."
        return ToolResult.ok(invocation.id, content, metadata={"matches": len(matches)})

    def _describe(self, invocation: ToolInvocation) -> ToolResult:
        server, tool = self._split(invocation.params["describe"])
        for t in self._cache.get(server):
            if t.name == tool:
                return ToolResult.ok(
                    invocation.id,
                    json.dumps(t.to_json(), indent=2),
                    metadata={"server": server, "tool": tool},
                )
        return ToolResult.error(invocation.id, f"no cached tool {tool!r} on server {server!r}")

    async def _connect(self, invocation: ToolInvocation) -> ToolResult:
        server = invocation.params["connect"]
        tools = await self._manager.refresh_tools(server)
        listing = "\n".join(f"- {t.name}: {t.description}" for t in tools) or "(no tools)"
        return ToolResult.ok(
            invocation.id,
            f"Connected to {server}. Tools:\n{listing}",
            metadata={"server": server, "tool_count": len(tools)},
        )

    async def _call_tool(self, invocation: ToolInvocation) -> ToolResult:
        params = invocation.params
        server, tool = self._split(params["tool"])

        raw_args = params.get("args") or "{}"
        try:
            arguments: dict[str, Any] = json.loads(raw_args)
        except json.JSONDecodeError as e:
            return ToolResult.error(invocation.id, f"args is not valid JSON: {e}")

        result = await self._manager.call_tool(server, tool, arguments)

        texts = [
            block.text for block in result.content if getattr(block, "type", None) == "text"
        ]
        content = "\n".join(texts) if texts else "(no text content)"
        content = guard_text(
            content, self._guard_config(), self._temp_dir, label=f"{server}-{tool}"
        )

        metadata = {"server": server, "tool": tool}
        if result.isError:
            return ToolResult.error(invocation.id, content, metadata=metadata)
        return ToolResult.ok(invocation.id, content, metadata=metadata)
