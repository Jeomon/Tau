"""mcp.json discovery, precedence merging, and variable expansion."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Lifecycle = Literal["lazy", "eager", "keep-alive"]
Transport = Literal["stdio", "http", "sse"]
AuthMode = Literal["none", "bearer", "oauth"]

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}|\$env:([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class ServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    transport: Transport = "stdio"
    lifecycle: Lifecycle = "lazy"
    idle_timeout: int = 10
    request_timeout_ms: int = 30000
    auth: AuthMode = "none"
    bearer_token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    direct_tools: bool | list[str] = False
    exclude_tools: list[str] = field(default_factory=list)
    oauth: dict[str, Any] = field(default_factory=dict)

    @property
    def is_remote(self) -> bool:
        return self.transport in ("http", "sse")


@dataclass
class OutputGuardConfig:
    max_bytes: int = 50 * 1024
    max_lines: int = 2000
    details_max_bytes: int = 4 * 1024


@dataclass
class McpSettings:
    tool_prefix: Literal["server", "short", "none"] = "server"
    idle_timeout: int = 10
    direct_tools: bool | list[str] = False
    output_guard: OutputGuardConfig = field(default_factory=OutputGuardConfig)


@dataclass
class McpConfig:
    servers: dict[str, ServerConfig] = field(default_factory=dict)
    settings: McpSettings = field(default_factory=McpSettings)


def _expand(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return os.environ.get(name, "")

    return _VAR_PATTERN.sub(repl, value)


def _expand_any(value: Any) -> Any:
    if isinstance(value, str):
        return _expand(value)
    if isinstance(value, list):
        return [_expand_any(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_any(v) for k, v in value.items()}
    return value


def config_search_paths(cwd: Path) -> list[Path]:
    """Precedence order, highest first: project override, project shared,
    global override, global shared."""
    return [
        cwd / ".tau" / "mcp.json",
        cwd / ".mcp.json",
        Path.home() / ".tau" / "mcp.json",
        Path.home() / ".config" / "mcp" / "mcp.json",
    ]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_server(name: str, raw: dict[str, Any]) -> ServerConfig:
    raw = _expand_any(raw)
    transport = raw.get("transport")
    if transport is None:
        transport = "http" if raw.get("url") else "stdio"

    direct_tools_raw = raw.get("directTools", False)

    return ServerConfig(
        name=name,
        command=raw.get("command"),
        args=list(raw.get("args", [])),
        env=dict(raw.get("env", {})),
        cwd=(str(Path(raw["cwd"]).expanduser()) if raw.get("cwd") else None),
        url=raw.get("url"),
        transport=transport,
        lifecycle=raw.get("lifecycle", "lazy"),
        idle_timeout=int(raw.get("idleTimeout", 10)),
        request_timeout_ms=int(raw.get("requestTimeoutMs", 30000)),
        auth=raw.get("auth", "none"),
        bearer_token=raw.get("bearerToken"),
        headers=dict(raw.get("headers", {})),
        direct_tools=direct_tools_raw,
        exclude_tools=list(raw.get("excludeTools", [])),
        oauth=dict(raw.get("oauth", {})),
    )


def _parse_settings(raw: dict[str, Any]) -> McpSettings:
    guard_raw = raw.get("outputGuard", True)
    if guard_raw is False:
        guard = OutputGuardConfig(max_bytes=1 << 62, max_lines=1 << 30)
    elif isinstance(guard_raw, dict):
        guard = OutputGuardConfig(
            max_bytes=int(guard_raw.get("maxBytes", 50 * 1024)),
            max_lines=int(guard_raw.get("maxLines", 2000)),
            details_max_bytes=int(guard_raw.get("detailsMaxBytes", 4 * 1024)),
        )
    else:
        guard = OutputGuardConfig()

    return McpSettings(
        tool_prefix=raw.get("toolPrefix", "server"),
        idle_timeout=int(raw.get("idleTimeout", 10)),
        direct_tools=raw.get("directTools", False),
        output_guard=guard,
    )


def load_config(cwd: Path) -> McpConfig:
    """Merge mcp.json sources by precedence; a server name defined in a
    higher-precedence file fully replaces the same name from a lower one."""
    servers: dict[str, ServerConfig] = {}
    settings_raw: dict[str, Any] = {}

    for path in reversed(config_search_paths(cwd)):
        if not path.is_file():
            continue
        data = _read_json(path)
        for name, raw in data.get("mcpServers", {}).items():
            try:
                servers[name] = _parse_server(name, raw)
            except (TypeError, ValueError, KeyError):
                continue
        if isinstance(data.get("settings"), dict):
            settings_raw.update(data["settings"])

    return McpConfig(servers=servers, settings=_parse_settings(settings_raw))
