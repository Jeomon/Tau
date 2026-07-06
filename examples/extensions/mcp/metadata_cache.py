"""Persisted tool metadata cache — lets direct tools register and the proxy
tool search/describe without a live server connection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CachedTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CachedTool:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
        )


@dataclass
class CachedServer:
    tools: list[CachedTool] = field(default_factory=list)
    updated_at: float = 0.0


class MetadataCache:
    """One cache file per config scope, keyed by server name."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._servers: dict[str, CachedServer] = {}
        self.load()

    def load(self) -> None:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._servers = {}
            return

        servers: dict[str, CachedServer] = {}
        for name, entry in data.get("servers", {}).items():
            tools = [CachedTool.from_json(t) for t in entry.get("tools", [])]
            servers[name] = CachedServer(tools=tools, updated_at=entry.get("updatedAt", 0.0))
        self._servers = servers

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "servers": {
                name: {
                    "tools": [t.to_json() for t in server.tools],
                    "updatedAt": server.updated_at,
                }
                for name, server in self._servers.items()
            }
        }
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self._path)

    def get(self, server: str) -> list[CachedTool]:
        entry = self._servers.get(server)
        return entry.tools if entry else []

    def all_tools(self) -> dict[str, list[CachedTool]]:
        return {name: entry.tools for name, entry in self._servers.items()}

    def update(self, server: str, tools: list[CachedTool]) -> None:
        self._servers[server] = CachedServer(tools=tools, updated_at=time.time())
        self.save()

    def clear(self, server: str | None = None) -> None:
        if server is None:
            self._servers.clear()
        else:
            self._servers.pop(server, None)
        self.save()
