"""Tests for tau/agent/types.py — AgentPhase, AgentContext, AgentConfig, ContextUsage."""
from __future__ import annotations

from pathlib import Path

from tau.agent.types import AgentPhase, AgentContext, AgentConfig, ContextUsage


class TestAgentPhase:
    def test_idle_value(self):
        assert AgentPhase.IDLE == "idle"

    def test_turn_value(self):
        assert AgentPhase.TURN == "turn"

    def test_is_str_enum(self):
        assert isinstance(AgentPhase.IDLE, str)

    def test_members(self):
        members = {m.value for m in AgentPhase}
        assert members == {"idle", "turn"}


class TestAgentContext:
    def test_construction_with_defaults(self):
        ctx = AgentContext(system_prompt="You are helpful.", messages=[])
        assert ctx.system_prompt == "You are helpful."
        assert ctx.messages == []
        assert ctx.tools == []

    def test_construction_with_tools(self):
        from tau.builtins.tools.read import ReadTool
        t = ReadTool()
        ctx = AgentContext(system_prompt="sp", messages=[], tools=[t])
        assert len(ctx.tools) == 1
        assert ctx.tools[0] is t

    def test_messages_stored(self):
        from tau.message.types import UserMessage, TextContent
        msg = UserMessage(contents=[TextContent(type="text", content="hi")])
        ctx = AgentContext(system_prompt="sp", messages=[msg])
        assert len(ctx.messages) == 1


class TestAgentConfig:
    def test_required_fields(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path)
        assert cfg.cwd == tmp_path

    def test_default_context_window(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path)
        assert cfg.context_window == 200_000

    def test_default_system_prompt_empty(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path)
        assert cfg.system_prompt == ""

    def test_default_model_is_none(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path)
        assert cfg.model is None

    def test_custom_system_prompt(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path, system_prompt="Custom")
        assert cfg.system_prompt == "Custom"

    def test_custom_context_window(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path, context_window=50_000)
        assert cfg.context_window == 50_000

    def test_cwd_is_path(self, tmp_path):
        cfg = AgentConfig(cwd=tmp_path)
        assert isinstance(cfg.cwd, Path)


class TestContextUsage:
    def test_basic_construction(self):
        cu = ContextUsage(tokens=1000, context_window=200_000)
        assert cu.tokens == 1000
        assert cu.context_window == 200_000
        assert cu.percent is None

    def test_with_percent(self):
        cu = ContextUsage(tokens=10_000, context_window=100_000, percent=10.0)
        assert cu.percent == 10.0

    def test_zero_tokens(self):
        cu = ContextUsage(tokens=0, context_window=200_000)
        assert cu.tokens == 0

    def test_full_context(self):
        cu = ContextUsage(tokens=200_000, context_window=200_000, percent=100.0)
        assert cu.percent == 100.0
