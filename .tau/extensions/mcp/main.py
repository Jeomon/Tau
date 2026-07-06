"""MCP (Model Context Protocol) client extension for Tau.

Registers a single token-cheap proxy tool (``mcp``) plus, once servers have
been connected at least once, any tools promoted via a server's
``directTools`` config. See config.py for the mcp.json schema.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from auth import AuthManager
from commands import register_commands
from config import load_config
from direct_tool import build_direct_tools
from metadata_cache import MetadataCache
from proxy_tool import McpProxyTool
from server_manager import McpServerManager

from tau.extensions.settings import ExtensionSettings
from tau.settings.paths import get_config_dir, get_temp_dir


@dataclass
class McpExtConfig:
    enabled: bool = True
    toolPrefix: str = "server"
    idleTimeout: str = "10"
    autoApproveElicitation: bool = False
    autoApproveSampling: bool = True


def register(tau):
    cwd = tau.cwd
    ext_settings = ExtensionSettings(McpExtConfig, tau.config)
    if not ext_settings.get("enabled", True):
        return

    mcp_config = load_config(cwd)
    cache = MetadataCache(get_config_dir(cwd) / "mcp-cache.json")
    temp_dir = get_temp_dir(cwd) / "mcp"
    auth_manager = AuthManager(get_config_dir(cwd) / "mcp-auth.json")

    from elicitation import make_elicitation_handler
    from sampling import make_sampling_handler

    sampling_handler = make_sampling_handler(
        tau, auto_approve=ext_settings.get("autoApproveSampling", True)
    )
    elicitation_handler = make_elicitation_handler(
        tau, auto_approve=ext_settings.get("autoApproveElicitation", False)
    )

    manager = McpServerManager(
        mcp_config,
        cache,
        auth_headers_provider=auth_manager.headers_for,
        sampling_handler=sampling_handler,
        elicitation_handler=elicitation_handler,
    )

    tau.provide("mcp_manager", manager)
    tau.provide("mcp_cache", cache)
    tau.provide("mcp_auth", auth_manager)

    tau.register_tool(McpProxyTool(manager, cache, temp_dir))

    for direct_tool in build_direct_tools(mcp_config, cache, manager, temp_dir):
        tau.register_tool(direct_tool)

    register_commands(tau, manager, cache, mcp_config)

    @tau.on("runtime_ready")
    async def _start(_event, _ctx):
        await manager.start_eager()

    @tau.on("runtime_stop")
    async def _stop(_event, _ctx):
        await manager.shutdown()
