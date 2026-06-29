"""Tests for tau/tool/registry.py — ToolRegistry."""

from __future__ import annotations

from tau.builtins.tools.edit import EditTool
from tau.builtins.tools.grep import GrepTool
from tau.builtins.tools.read import ReadTool
from tau.builtins.tools.write import WriteTool
from tau.tool.registry import ToolRegistry


def _reg(*tools, source="builtin") -> ToolRegistry:
    r = ToolRegistry()
    for t in tools:
        r.register(t, source=source)
    return r


class TestToolRegistryRegister:
    def test_register_adds_tool(self):
        r = ToolRegistry()
        r.register(ReadTool())
        assert r.get("read") is not None

    def test_register_last_wins_on_collision(self):
        r = ToolRegistry()
        t1 = ReadTool()
        t2 = ReadTool()
        r.register(t1)
        r.register(t2)
        assert r.get("read") is t2

    def test_len_reflects_registered_count(self):
        r = _reg(ReadTool(), WriteTool(), EditTool())
        assert len(r) == 3

    def test_contains_registered_name(self):
        r = _reg(ReadTool())
        assert "read" in r

    def test_not_contains_unregistered(self):
        r = ToolRegistry()
        assert "read" not in r


class TestToolRegistryUnregister:
    def test_unregister_removes_tool(self):
        r = _reg(ReadTool())
        assert r.unregister("read") is True
        assert r.get("read") is None

    def test_unregister_nonexistent_returns_false(self):
        r = ToolRegistry()
        assert r.unregister("nonexistent") is False

    def test_len_decreases_after_unregister(self):
        r = _reg(ReadTool(), WriteTool())
        r.unregister("read")
        assert len(r) == 1


class TestToolRegistryGet:
    def test_get_returns_none_for_unknown(self):
        r = ToolRegistry()
        assert r.get("unknown") is None

    def test_get_returns_correct_tool(self):
        t = ReadTool()
        r = ToolRegistry()
        r.register(t)
        assert r.get("read") is t


class TestToolRegistryList:
    def test_list_all_tools(self):
        r = _reg(ReadTool(), WriteTool())
        names = {t.name for t in r.list()}
        assert "read" in names
        assert "write" in names

    def test_list_filtered_by_source(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="builtin")
        r.register(WriteTool(), source="extension")
        builtins = r.list(source="builtin")
        assert len(builtins) == 1
        assert builtins[0].name == "read"

    def test_list_empty_when_no_tools(self):
        r = ToolRegistry()
        assert r.list() == []


class TestToolRegistryNames:
    def test_names_returns_set_of_all(self):
        r = _reg(ReadTool(), WriteTool())
        assert r.names() == {"read", "write"}

    def test_names_filtered_by_source(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="builtin")
        r.register(WriteTool(), source="mcp")
        assert r.names(source="builtin") == {"read"}

    def test_names_empty_when_no_tools(self):
        r = ToolRegistry()
        assert r.names() == set()


class TestToolRegistrySources:
    def test_sources_returns_all_source_labels(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="builtin")
        r.register(WriteTool(), source="extension")
        assert r.sources() == {"builtin", "extension"}

    def test_sources_empty_when_no_tools(self):
        r = ToolRegistry()
        assert r.sources() == set()


class TestToolRegistryReplaceSource:
    def test_replace_source_swaps_tools(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="mcp")
        r.register(WriteTool(), source="mcp")
        r.replace_source("mcp", [EditTool()])
        assert r.get("edit") is not None
        assert r.get("read") is None
        assert r.get("write") is None

    def test_replace_source_leaves_other_sources_intact(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="builtin")
        r.register(WriteTool(), source="extension")
        r.replace_source("extension", [GrepTool()])
        assert r.get("read") is not None
        assert r.get("grep") is not None
        assert r.get("write") is None

    def test_replace_with_empty_removes_all_from_source(self):
        r = ToolRegistry()
        r.register(ReadTool(), source="mcp")
        r.replace_source("mcp", [])
        assert r.get("read") is None
        assert len(r) == 0

    def test_removing_override_restores_shadowed_builtin(self):
        r = ToolRegistry()
        builtin = ReadTool()
        override = ReadTool()
        r.register(builtin, source="builtin")
        r.register(override, source="extension")

        r.replace_source("extension", [])

        assert r.get("read") is builtin


class TestToolRegistryContainerProtocol:
    def test_len_empty(self):
        assert len(ToolRegistry()) == 0

    def test_len_after_register(self):
        r = _reg(ReadTool(), WriteTool())
        assert len(r) == 2

    def test_len_decreases_on_unregister(self):
        r = _reg(ReadTool(), WriteTool())
        r.unregister("read")
        assert len(r) == 1

    def test_contains_registered_tool(self):
        r = _reg(ReadTool())
        assert "read" in r

    def test_not_contains_unregistered_tool(self):
        r = ToolRegistry()
        assert "read" not in r

    def test_contains_after_unregister(self):
        r = _reg(ReadTool())
        r.unregister("read")
        assert "read" not in r
