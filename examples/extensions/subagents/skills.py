from __future__ import annotations

from pathlib import Path

from tau.skills.loader import load_skills_from_dir
from tau.skills.types import Skill


def _available_skills(cwd: Path) -> dict[str, Skill]:
    from tau.builtins import __file__ as builtins_file

    roots = [
        Path(builtins_file).parent / "skills",
        Path.home() / ".tau" / "skills",
        cwd / ".tau" / "skills",
    ]
    skills: dict[str, Skill] = {}
    for root in roots:
        skills.update(load_skills_from_dir(root).skills)
    return skills


def build_skills_block(skill_spec: list[str] | str, cwd: Path) -> str:
    """Load requested skills and return their complete prompt content."""
    if not skill_spec:
        return ""
    available = _available_skills(cwd)
    names = list(available) if skill_spec == "all" else skill_spec
    selected = [available[name] for name in names if name in available]
    if not selected:
        return ""
    parts = ["# Preloaded Skills"]
    for skill in selected:
        parts.extend(["", f"## {skill.name}", skill.content])
    return "\n".join(parts)
