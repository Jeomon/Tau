"""Tests for tau/skills/registry.py — SkillRegistry."""

from __future__ import annotations

from tau.skills.registry import SkillRegistry
from tau.skills.types import Skill


def _skill(
    name: str,
    description: str = "A skill",
    content: str = "# Skill",
    disable_model_invocation: bool = False,
    file_path: str = "/fake/skill.md",
) -> Skill:
    return Skill(
        name=name,
        description=description,
        content=content,
        file_path=file_path,
        base_dir="/fake",
        disable_model_invocation=disable_model_invocation,
    )


def _registry(*skills: Skill) -> SkillRegistry:
    r = SkillRegistry()
    r._registry.clear()
    r._builtins_loaded = True
    for s in skills:
        r._registry[s.name] = s
    return r


class TestSkillRegistryList:
    def test_list_excludes_disabled(self):
        r = _registry(
            _skill("enabled"),
            _skill("disabled", disable_model_invocation=True),
        )
        visible = r.list()
        names = [s.name for s in visible]
        assert "enabled" in names
        assert "disabled" not in names

    def test_list_all_includes_disabled(self):
        r = _registry(
            _skill("enabled"),
            _skill("disabled", disable_model_invocation=True),
        )
        all_skills = r.list_all()
        names = [s.name for s in all_skills]
        assert "enabled" in names
        assert "disabled" in names

    def test_list_empty_registry(self):
        r = _registry()
        assert r.list() == []

    def test_list_all_skills_when_none_disabled(self):
        r = _registry(_skill("a"), _skill("b"), _skill("c"))
        assert len(r.list()) == 3


class TestSkillRegistryFormatForSystemPrompt:
    def test_empty_skills_returns_empty_string(self):
        r = _registry()
        result = r.format_for_system_prompt([])
        assert result == ""

    def test_all_disabled_returns_empty_string(self):
        s = _skill("disabled", disable_model_invocation=True)
        r = _registry(s)
        result = r.format_for_system_prompt([s])
        assert result == ""

    def test_xml_block_structure(self):
        s = _skill("deploy", description="Deploy the application", file_path="/skills/deploy.md")
        r = _registry(s)
        result = r.format_for_system_prompt([s])
        assert "<available_skills>" in result
        assert "</available_skills>" in result
        assert "<name>deploy</name>" in result
        assert "<description>Deploy the application</description>" in result
        assert "<location>/skills/deploy.md</location>" in result

    def test_multiple_skills_all_appear(self):
        skills = [
            _skill("test", description="Run tests"),
            _skill("deploy", description="Deploy"),
        ]
        r = _registry(*skills)
        result = r.format_for_system_prompt(skills)
        assert "<name>test</name>" in result
        assert "<name>deploy</name>" in result

    def test_each_skill_has_skill_tags(self):
        s = _skill("build")
        r = _registry(s)
        result = r.format_for_system_prompt([s])
        assert "<skill>" in result
        assert "</skill>" in result

    def test_includes_usage_instructions(self):
        s = _skill("build")
        r = _registry(s)
        result = r.format_for_system_prompt([s])
        assert "read tool" in result.lower()

    def test_mixed_enabled_disabled_only_enabled_appear(self):
        enabled = _skill("enabled_skill")
        disabled = _skill("disabled_skill", disable_model_invocation=True)
        r = _registry(enabled, disabled)
        result = r.format_for_system_prompt([enabled, disabled])
        assert "<name>enabled_skill</name>" in result
        assert "<name>disabled_skill</name>" not in result


class TestSkillRegistryRegisterGet:
    def test_get_registered_skill(self):
        r = _registry()
        s = _skill("foo")
        r._registry["foo"] = s
        assert r.get("foo") is s

    def test_get_unknown_returns_none(self):
        r = _registry()
        assert r.get("unknown") is None
