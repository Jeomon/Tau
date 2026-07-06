"""Transport selection for MCP client connections (stdio / HTTP / SSE)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from config import ServerConfig
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client  # deprecated in favor of an

# httpx.AsyncClient-based API, but still the simplest way to pass headers/timeout directly.
from mcp.shared.message import SessionMessage

RWStreams = tuple[
    MemoryObjectReceiveStream[SessionMessage | Exception],
    MemoryObjectSendStream[SessionMessage],
]


@asynccontextmanager
async def open_transport(
    server: ServerConfig, headers: dict[str, str] | None = None
) -> AsyncGenerator[RWStreams]:
    """Yield (read_stream, write_stream) for the configured transport."""
    merged_headers = {**server.headers, **(headers or {})}

    if server.transport == "stdio":
        if not server.command:
            raise ValueError(f"server {server.name!r} has no command for stdio transport")
        params = StdioServerParameters(
            command=server.command,
            args=server.args,
            env={**server.env} or None,
            cwd=server.cwd,
        )
        async with stdio_client(params) as (read, write):
            yield read, write
        return

    if not server.url:
        raise ValueError(f"server {server.name!r} has no url for {server.transport} transport")

    timeout = server.request_timeout_ms / 1000

    if server.transport == "http":
        async with streamablehttp_client(
            server.url, headers=merged_headers or None, timeout=timeout
        ) as (read, write, _):
            yield read, write
        return

    if server.transport == "sse":
        async with sse_client(server.url, headers=merged_headers or None, timeout=timeout) as (
            read,
            write,
        ):
            yield read, write
        return

    raise ValueError(f"unknown transport {server.transport!r} for server {server.name!r}")
