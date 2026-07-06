"""Promote specific cached MCP tools to native Tau tools (bypassing the proxy)
for better model-side discoverability, per a server's ``directTools`` config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import McpConfig
from metadata_cache import CachedTool, MetadataCache
from output_guard import guard_text
from pydantic import BaseModel, ConfigDict, Field, create_model
from server_manager import McpServerManager

from tau.tool.types import (
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


class _PermissiveModel(BaseModel):
    """Fallback schema for tools whose inputSchema has no translatable properties."""

    model_config = ConfigDict(extra="allow")


_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _field_type(schema: dict[str, Any]) -> Any:
    json_type = schema.get("type")
    if isinstance(json_type, list):
        non_null = [t for t in json_type if t != "null"]
        json_type = non_null[0] if non_null else None
    if not isinstance(json_type, str):
        return Any
    return _JSON_TYPE_MAP.get(json_type, Any)


def schema_to_model(name: str, input_schema: dict[str, Any]) -> type[BaseModel]:
    """Best-effort JSON-schema -> Pydantic model conversion for one MCP tool's
    inputSchema. Falls back to `Any` for properties it can't translate."""
    properties: dict[str, dict[str, Any]] = input_schema.get("properties") or {}
    required = set(input_schema.get("required", []) or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _field_type(prop_schema)
        description: str = prop_schema.get("description", "")
        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., description=description))
        else:
            fields[prop_name] = (py_type | None, Field(default=None, description=description))

    if not fields:
        # Permissive fallback for schemas we can't fully translate.
        return create_model(f"_McpDirect_{name}", __base__=_PermissiveModel)

    model_name = f"_McpDirect_{name}"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


class DynamicMcpTool(Tool):
    def __init__(
        self,
        server: str,
        cached: CachedTool,
        manager: McpServerManager,
        temp_dir: Path,
        *,
        display_name: str,
    ) -> None:
        super().__init__(
            name=display_name,
            description=f"[MCP:{server}] {cached.description}",
            schema=schema_to_model(f"{server}_{cached.name}", cached.input_schema),
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._server = server
        self._tool = cached.name
        self._manager = manager
        self._temp_dir = temp_dir

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        result = await self._manager.call_tool(self._server, self._tool, invocation.params)
        texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        content = "\n".join(texts) if texts else "(no text content)"
        guard = self._manager.config.settings.output_guard
        content = guard_text(content, guard, self._temp_dir, label=f"{self._server}-{self._tool}")

        if result.isError:
            return ToolResult.error(invocation.id, content)
        return ToolResult.ok(invocation.id, content)


def _display_name(prefix_mode: str, server: str, tool: str) -> str:
    if prefix_mode == "none":
        return tool
    if prefix_mode == "short":
        return f"{server[:3]}_{tool}"
    return f"{server}_{tool}"


def build_direct_tools(
    config: McpConfig,
    cache: MetadataCache,
    manager: McpServerManager,
    temp_dir: Path,
) -> list[DynamicMcpTool]:
    tools: list[DynamicMcpTool] = []
    prefix_mode = config.settings.tool_prefix

    for server_name, server_cfg in config.servers.items():
        direct_spec = server_cfg.direct_tools
        if direct_spec is False:
            continue

        for cached in cache.get(server_name):
            if isinstance(direct_spec, list) and cached.name not in direct_spec:
                continue
            display_name = _display_name(prefix_mode, server_name, cached.name)
            tools.append(
                DynamicMcpTool(server_name, cached, manager, temp_dir, display_name=display_name)
            )

    return tools
