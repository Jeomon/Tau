from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

# Add the project-level mcp extension directory to sys.path to allow imports under test.
sys.path.insert(0, str(Path(__file__).parent.parent / ".tau" / "extensions" / "mcp"))

from config import McpConfig, McpSettings, OutputGuardConfig, ServerConfig, load_config
from direct_tool import build_direct_tools, schema_to_model
from metadata_cache import MetadataCache
from output_guard import guard_text
from proxy_tool import McpProxyTool
from server_manager import McpServerManager

from tau.tool.types import ToolInvocation

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "dummy_mcp_server.py"


# ── config precedence ─────────────────────────────────────────────────────


def test_config_precedence_project_overrides_shared(tmp_path: Path) -> None:
    (tmp_path / ".tau").mkdir()
    (tmp_path / ".tau" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "project-cmd"}}})
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "shared-cmd"}, "other": {"command": "x"}}})
    )

    config = load_config(tmp_path)
    assert config.servers["srv"].command == "project-cmd"
    assert config.servers["other"].command == "x"


def test_config_env_var_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret123")
    (tmp_path / ".tau").mkdir()
    (tmp_path / ".tau" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "cmd", "bearerToken": "${MY_TOKEN}"}}})
    )

    config = load_config(tmp_path)
    assert config.servers["srv"].bearer_token == "secret123"


def test_config_output_guard_settings(tmp_path: Path) -> None:
    (tmp_path / ".tau").mkdir()
    guard_settings = {"outputGuard": {"maxBytes": 100, "maxLines": 5}}
    (tmp_path / ".tau" / "mcp.json").write_text(
        json.dumps({"mcpServers": {}, "settings": guard_settings})
    )
    config = load_config(tmp_path)
    assert config.settings.output_guard.max_bytes == 100
    assert config.settings.output_guard.max_lines == 5


# ── output guard ───────────────────────────────────────────────────────────


def test_output_guard_passes_small_text(tmp_path: Path) -> None:
    guard = OutputGuardConfig(max_bytes=1000, max_lines=100)
    text = "short output"
    assert guard_text(text, guard, tmp_path) == text


def test_output_guard_truncates_and_spills(tmp_path: Path) -> None:
    guard = OutputGuardConfig(max_bytes=50, max_lines=3)
    text = "\n".join(f"line {i}" for i in range(20))
    result = guard_text(text, guard, tmp_path, label="test")

    assert "truncated" in result
    spilled = list(tmp_path.glob("test-*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text() == text


# ── JSON schema -> pydantic conversion ─────────────────────────────────────


def test_schema_to_model_required_and_optional_fields() -> None:
    model = schema_to_model(
        "add",
        {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "first"},
                "b": {"type": "integer", "description": "second"},
                "note": {"type": "string"},
            },
            "required": ["a", "b"],
        },
    )
    instance = model(a=1, b=2)
    assert instance.a == 1
    assert instance.note is None

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        model(a=1)  # missing required 'b'


def test_schema_to_model_permissive_fallback() -> None:
    model = schema_to_model("mystery", {"type": "object"})
    instance = model(anything="goes")
    assert instance.anything == "goes"  # type: ignore[attr-defined]


# ── integration: real stdio MCP server via the dummy fixture ──────────────


def _server_config(name: str = "dummy") -> ServerConfig:
    return ServerConfig(
        name=name,
        command=sys.executable,
        args=[str(FIXTURE_SERVER)],
        lifecycle="lazy",
        idle_timeout=9999,
        direct_tools=True,
    )


def test_proxy_tool_end_to_end(tmp_path: Path) -> None:
    async def exercise() -> None:
        mcp_config = McpConfig(
            servers={"dummy": _server_config()},
            settings=McpSettings(output_guard=OutputGuardConfig()),
        )
        cache = MetadataCache(tmp_path / "cache.json")
        manager = McpServerManager(mcp_config, cache)
        proxy = McpProxyTool(manager, cache, tmp_path / "spill")

        try:
            connect_result = await proxy.execute(
                ToolInvocation(id="1", name="mcp", cwd=tmp_path, params={"connect": "dummy"})
            )
            assert not connect_result.is_error
            assert "add" in connect_result.content

            search_result = await proxy.execute(
                ToolInvocation(id="2", name="mcp", cwd=tmp_path, params={"search": "add"})
            )
            assert "dummy.add" in search_result.content

            describe_result = await proxy.execute(
                ToolInvocation(id="3", name="mcp", cwd=tmp_path, params={"describe": "dummy.add"})
            )
            assert not describe_result.is_error
            assert "a" in describe_result.content

            call_result = await proxy.execute(
                ToolInvocation(
                    id="4",
                    name="mcp",
                    cwd=tmp_path,
                    params={"tool": "dummy.add", "args": json.dumps({"a": 2, "b": 3})},
                )
            )
            assert not call_result.is_error
            assert "5" in call_result.content

            direct_tools = build_direct_tools(mcp_config, cache, manager, tmp_path / "spill")
            names = {t.name for t in direct_tools}
            assert "dummy_add" in names
            assert "dummy_echo" in names

            echo_tool = next(t for t in direct_tools if t.name == "dummy_echo")
            echo_result = await echo_tool.execute(
                ToolInvocation(id="5", name="dummy_echo", cwd=tmp_path, params={"text": "hi"})
            )
            assert not echo_result.is_error
            assert "hi" in echo_result.content
        finally:
            await manager.shutdown()

    asyncio.run(exercise())
