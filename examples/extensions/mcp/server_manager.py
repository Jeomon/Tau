"""Per-server connection lifecycle: lazy/eager/keep-alive connect, idle
disconnect, and the call surface used by the proxy and direct tools."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from config import McpConfig, ServerConfig
from mcp import ClientSession
from metadata_cache import CachedTool, MetadataCache
from transports import open_transport

logger = logging.getLogger("tau.mcp")

SamplingHandler = Callable[[types.CreateMessageRequestParams], Awaitable[types.CreateMessageResult]]
ElicitationHandler = Callable[[types.ElicitRequestParams], Awaitable[types.ElicitResult]]

_HEALTH_CHECK_INTERVAL = 30.0
_IDLE_CHECK_INTERVAL = 5.0


class McpServerError(Exception):
    pass


@dataclass
class ServerStatus:
    name: str
    lifecycle: str
    connected: bool
    tool_count: int
    last_error: str | None = None


class McpServerHandle:
    """Owns one server's connection. The transport + ClientSession context
    managers are kept open for the lifetime of the connection by a dedicated
    background task (``_owner_task``); other coroutines call session methods
    directly since anyio/ClientSession allow that from any task on the same
    event loop while the owning context manager is still entered."""

    def __init__(
        self,
        config: ServerConfig,
        *,
        auth_headers_provider: Callable[[ServerConfig], Awaitable[dict[str, str]]] | None = None,
        sampling_handler: SamplingHandler | None = None,
        elicitation_handler: ElicitationHandler | None = None,
    ) -> None:
        self.config = config
        self._auth_headers_provider = auth_headers_provider
        self._sampling_handler = sampling_handler
        self._elicitation_handler = elicitation_handler

        self._session: ClientSession | None = None
        self._owner_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._connect_error: str | None = None
        self._last_used = 0.0
        self._idle_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ── connection lifecycle ────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def _sampling_cb(
        self, context: Any, params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult | types.ErrorData:
        if self._sampling_handler is None:
            return types.ErrorData(code=types.INVALID_REQUEST, message="sampling not supported")
        try:
            return await self._sampling_handler(params)
        except Exception as e:  # noqa: BLE001
            return types.ErrorData(code=types.INTERNAL_ERROR, message=str(e))

    async def _elicitation_cb(
        self, context: Any, params: types.ElicitRequestParams
    ) -> types.ElicitResult | types.ErrorData:
        if self._elicitation_handler is None:
            return types.ElicitResult(action="decline")
        try:
            return await self._elicitation_handler(params)
        except Exception as e:  # noqa: BLE001
            return types.ErrorData(code=types.INTERNAL_ERROR, message=str(e))

    async def _owner(self) -> None:
        headers: dict[str, str] = {}
        if self.config.is_remote and self._auth_headers_provider is not None:
            headers = await self._auth_headers_provider(self.config)

        try:
            async with (
                open_transport(self.config, headers=headers) as (read, write),
                ClientSession(
                    read,
                    write,
                    sampling_callback=self._sampling_cb,
                    elicitation_callback=self._elicitation_cb,
                ) as session,
            ):
                await session.initialize()
                self._session = session
                self._connect_error = None
                self._last_used = time.monotonic()
                self._ready.set()
                await self._stop.wait()
        except Exception as e:  # noqa: BLE001
            self._connect_error = str(e)
            logger.warning("mcp server %s: connection failed: %s", self.config.name, e)
        finally:
            self._session = None
            self._ready.set()

    async def ensure_connected(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._owner_task = asyncio.create_task(self._owner())
            await self._ready.wait()
            if self._session is None:
                raise McpServerError(self._connect_error or "connection failed")
            if self.config.lifecycle == "lazy" and self.config.idle_timeout > 0:
                self._idle_task = asyncio.create_task(self._idle_watch())

    async def disconnect(self) -> None:
        async with self._lock:
            if self._idle_task is not None:
                self._idle_task.cancel()
                self._idle_task = None
            if self._owner_task is not None:
                self._stop.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._owner_task
                self._owner_task = None
            self._session = None

    async def _idle_watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(_IDLE_CHECK_INTERVAL)
                if self._session is None:
                    return
                if time.monotonic() - self._last_used >= self.config.idle_timeout:
                    logger.info("mcp server %s: idle timeout, disconnecting", self.config.name)
                    asyncio.create_task(self.disconnect())
                    return
        except asyncio.CancelledError:
            return

    async def health_check(self) -> bool:
        if self._session is None:
            return False
        try:
            await self._session.send_ping()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── calls ────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[CachedTool]:
        await self.ensure_connected()
        assert self._session is not None
        self._last_used = time.monotonic()
        result = await self._session.list_tools()
        excluded = set(self.config.exclude_tools)
        return [
            CachedTool(name=t.name, description=t.description or "", input_schema=t.inputSchema)
            for t in result.tools
            if t.name not in excluded
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        await self.ensure_connected()
        assert self._session is not None
        self._last_used = time.monotonic()
        return await self._session.call_tool(name, arguments)


class McpServerManager:
    """Holds all configured servers for the active project/session."""

    def __init__(
        self,
        mcp_config: McpConfig,
        cache: MetadataCache,
        *,
        auth_headers_provider: Callable[[ServerConfig], Awaitable[dict[str, str]]] | None = None,
        sampling_handler: SamplingHandler | None = None,
        elicitation_handler: ElicitationHandler | None = None,
    ) -> None:
        self.config = mcp_config
        self.cache = cache
        self._auth_headers_provider = auth_headers_provider
        self._sampling_handler = sampling_handler
        self._elicitation_handler = elicitation_handler
        self._handles: dict[str, McpServerHandle] = {
            name: McpServerHandle(
                cfg,
                auth_headers_provider=auth_headers_provider,
                sampling_handler=sampling_handler,
                elicitation_handler=elicitation_handler,
            )
            for name, cfg in mcp_config.servers.items()
        }
        self._keepalive_tasks: list[asyncio.Task] = []

    def names(self) -> list[str]:
        return list(self._handles.keys())

    def get(self, name: str) -> McpServerHandle:
        handle = self._handles.get(name)
        if handle is None:
            raise McpServerError(f"unknown mcp server: {name!r}")
        return handle

    async def start_eager(self) -> None:
        """Connect eager/keep-alive servers and start keep-alive health checks."""
        for name, handle in self._handles.items():
            if handle.config.lifecycle in ("eager", "keep-alive"):
                try:
                    await handle.ensure_connected()
                    await self.refresh_tools(name)
                except McpServerError as e:
                    logger.warning("mcp server %s: eager connect failed: %s", name, e)
                if handle.config.lifecycle == "keep-alive":
                    self._keepalive_tasks.append(asyncio.create_task(self._keepalive(name, handle)))

    async def _keepalive(self, name: str, handle: McpServerHandle) -> None:
        try:
            while True:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
                if not await handle.health_check():
                    logger.info("mcp server %s: health check failed, reconnecting", name)
                    await handle.disconnect()
                    try:
                        await handle.ensure_connected()
                    except McpServerError as e:
                        logger.warning("mcp server %s: reconnect failed: %s", name, e)
        except asyncio.CancelledError:
            return

    async def shutdown(self) -> None:
        for task in self._keepalive_tasks:
            task.cancel()
        for task in self._keepalive_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._keepalive_tasks.clear()
        for handle in self._handles.values():
            await handle.disconnect()

    async def refresh_tools(self, name: str) -> list[CachedTool]:
        handle = self.get(name)
        tools = await handle.list_tools()
        self.cache.update(name, tools)
        return tools

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        return await self.get(server).call_tool(tool, arguments)

    def status(self) -> list[ServerStatus]:
        out = []
        for name, handle in self._handles.items():
            out.append(
                ServerStatus(
                    name=name,
                    lifecycle=handle.config.lifecycle,
                    connected=handle.connected,
                    tool_count=len(self.cache.get(name)),
                    last_error=handle._connect_error,
                )
            )
        return out
