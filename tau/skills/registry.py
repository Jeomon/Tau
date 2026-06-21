"""
Skill registry.

Priority (highest wins):
    project  (.tau/skills/ relative to cwd)
    global   (~/.tau/skills/)
    builtin  (tau/builtins/skills/)

Skills are listed in the system prompt as <available_skills> XML so the model
can load them on demand via the read tool, or invoked explicitly with
/skill:name [args].
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from tau.core.registry import Registry
from tau.skills.loader import load_skills_from_dir
from tau.skills.types import Skill, SkillLoadError


class SkillRegistry(Registry[Skill, SkillLoadError]):
    def _load_from_dir(self, path: Path) -> Any:
        return load_skills_from_dir(path)

    def _get_dir(self, cwd: Path | None = None) -> Path:
        from tau.settings.paths import get_skills_dir

        return get_skills_dir(cwd)

    def _builtins_subdir(self) -> str:
        return "skills"

    def _extract_items(self, result: Any) -> dict[str, Skill]:
        return result.skills

    def _extract_errors(self, result: Any) -> list[SkillLoadError]:
        return result.errors

    def list(self) -> list[Skill]:
        """Return all skills available to the model (excluding disabled ones)."""
        self._ensure_builtins()
        return [s for s in self._registry.values() if not s.disable_model_invocation]

    def list_all(self) -> builtins.list[Skill]:
        """Return all registered skills, including disabled ones."""
        self._ensure_builtins()
        return list(self._registry.values())

    def format_for_system_prompt(self, skills: builtins.list[Skill]) -> str:
        """Format a skill list as XML for inclusion in the system prompt."""
        if not skills:
            return ""
        visible = [s for s in skills if not s.disable_model_invocation]
        if not visible:
            return ""
        lines = ["<available_skills>"]
        for s in visible:
            lines.append("  <skill>")
            lines.append(f"    <name>{s.name}</name>")
            lines.append(f"    <description>{s.description}</description>")
            lines.append(f"    <location>{s.file_path}</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        lines.append("")
        lines.append(
            "When a task matches a skill's description, use the read tool to load the skill file"
            " and follow its instructions."
        )
        return "\n".join(lines)


skill_registry = SkillRegistry()
