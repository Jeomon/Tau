"""/mcp slash command family: status, setup, tools, reconnect, logout."""

from __future__ import annotations

from config import McpConfig
from metadata_cache import MetadataCache
from server_manager import McpServerError, McpServerManager


def _print_or_notify(ctx, text: str) -> None:
    if ctx.ui is not None:
        ctx.ui.notify(text)
    else:
        print(text)


async def _cmd_status(ctx, manager: McpServerManager) -> None:
    statuses = manager.status()
    if not statuses:
        _print_or_notify(ctx, "No MCP servers configured. Add one to .tau/mcp.json.")
        return
    lines = ["MCP servers:"]
    for s in statuses:
        state = "connected" if s.connected else "disconnected"
        err = f" — {s.last_error}" if s.last_error else ""
        lines.append(f"  {s.name}  [{s.lifecycle}]  {state}  {s.tool_count} tools{err}")
    _print_or_notify(ctx, "\n".join(lines))


async def _cmd_setup(ctx, manager: McpServerManager) -> None:
    names = manager.names()
    if not names:
        _print_or_notify(
            ctx, "No MCP servers configured. Add one to .tau/mcp.json, then run /mcp setup again."
        )
        return

    results = []
    for name in names:
        try:
            tools = await manager.refresh_tools(name)
            results.append(f"  {name}: connected, {len(tools)} tools cached")
        except McpServerError as e:
            results.append(f"  {name}: FAILED — {e}")

    summary = (
        "MCP setup:\n" + "\n".join(results) + "\n\nRun /reload to pick up any new direct tools."
    )
    _print_or_notify(ctx, summary)
    await ctx.reload()


async def _cmd_tools(ctx, cache: MetadataCache) -> None:
    all_tools = cache.all_tools()
    if not any(all_tools.values()):
        _print_or_notify(ctx, "No cached tools. Run /mcp setup first.")
        return
    lines = []
    for server, tools in all_tools.items():
        for t in tools:
            lines.append(f"  {server}.{t.name}: {t.description}")
    _print_or_notify(ctx, "MCP tools:\n" + "\n".join(lines))


async def _cmd_reconnect(ctx, manager: McpServerManager, args: list[str]) -> None:
    targets = [args[0]] if args else manager.names()
    results = []
    for name in targets:
        try:
            await manager.get(name).disconnect()
            tools = await manager.refresh_tools(name)
            results.append(f"  {name}: reconnected, {len(tools)} tools")
        except McpServerError as e:
            results.append(f"  {name}: FAILED — {e}")
    _print_or_notify(ctx, "\n".join(results))


async def _cmd_logout(ctx, auth_manager, args: list[str]) -> None:
    if not args:
        _print_or_notify(ctx, "Usage: /mcp logout <server>")
        return
    auth_manager.logout(args[0])
    _print_or_notify(ctx, f"Cleared stored credentials for {args[0]}.")


async def _cmd_login(ctx, auth_manager, manager: McpServerManager, args: list[str]) -> None:
    if not args:
        _print_or_notify(ctx, "Usage: /mcp login <server>")
        return
    name = args[0]
    try:
        server_cfg = manager.get(name).config
    except McpServerError as e:
        _print_or_notify(ctx, str(e))
        return
    _print_or_notify(ctx, f"Opening browser to authenticate {name}...")
    try:
        await auth_manager.login(server_cfg)
    except Exception as e:  # noqa: BLE001
        _print_or_notify(ctx, f"Login failed: {e}")
        return
    _print_or_notify(ctx, f"{name}: logged in.")


def register_commands(
    tau, manager: McpServerManager, cache: MetadataCache, _config: McpConfig
) -> None:
    async def cmd_mcp(ctx, args: list[str]) -> None:
        sub = args[0] if args else ""
        rest = args[1:]

        if sub == "setup":
            await _cmd_setup(ctx, manager)
        elif sub == "tools":
            await _cmd_tools(ctx, cache)
        elif sub == "reconnect":
            await _cmd_reconnect(ctx, manager, rest)
        elif sub == "logout":
            auth_manager = tau.get_service("mcp_auth")
            await _cmd_logout(ctx, auth_manager, rest)
        elif sub == "login":
            auth_manager = tau.get_service("mcp_auth")
            await _cmd_login(ctx, auth_manager, manager, rest)
        else:
            await _cmd_status(ctx, manager)

    tau.register_command(
        "mcp",
        "MCP server status, setup, tools, reconnect, logout",
        cmd_mcp,
        argument_hint="[setup|tools|reconnect|login|logout] [server]",
    )
