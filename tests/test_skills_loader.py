"""Tests for tau/skills/loader.py — skill loading from files and directories."""

from __future__ import annotations

from pathlib import Path

from tau.skills.loader import load_skill_from_file, load_skills_from_dir


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadSkillFromFile:
    def test_minimal_valid_skill(self, tmp_path):
        f = _write(tmp_path / "test.md", "---\ndescription: A test skill\n---\nDo the thing.")
        skill, err = load_skill_from_file(f)
        assert err is None
        assert skill is not None
        assert skill.description == "A test skill"
        assert skill.content == "Do the thing."

    def test_name_from_frontmatter(self, tmp_path):
        f = _write(tmp_path / "s.md", "---\nname: my-skill\ndescription: desc\n---\nbody")
        skill, _ = load_skill_from_file(f)
        assert skill is not None
        assert skill.name == "my-skill"

    def test_name_from_stem_when_not_in_frontmatter(self, tmp_path):
        f = _write(tmp_path / "AutoFormat.md", "---\ndescription: d\n---\nbody")
        skill, _ = load_skill_from_file(f)
        assert skill is not None
        assert skill.name == "autoformat"

    def test_name_hint_used_when_no_name(self, tmp_path):
        f = _write(tmp_path / "s.md", "---\ndescription: d\n---\nbody")
        skill, _ = load_skill_from_file(f, name_hint="custom-name")
        assert skill is not None
        assert skill.name == "custom-name"

    def test_missing_description_returns_error(self, tmp_path):
        f = _write(tmp_path / "no_desc.md", "---\nname: test\n---\nbody")
        skill, err = load_skill_from_file(f)
        assert skill is None
        assert err is not None
        assert "description" in err

    def test_empty_body_returns_error(self, tmp_path):
        f = _write(tmp_path / "empty.md", "---\ndescription: d\n---\n")
        skill, err = load_skill_from_file(f)
        assert skill is None
        assert err is not None

    def test_no_frontmatter_no_description_returns_error(self, tmp_path):
        f = _write(tmp_path / "plain.md", "Just some content with no frontmatter.")
        skill, err = load_skill_from_file(f)
        assert skill is None
        assert err is not None

    def test_disable_model_invocation_flag(self, tmp_path):
        f = _write(
            tmp_path / "s.md", "---\ndescription: d\ndisable-model-invocation: true\n---\nbody"
        )
        skill, _ = load_skill_from_file(f)
        assert skill is not None
        assert skill.disable_model_invocation is True

    def test_file_path_set(self, tmp_path):
        f = _write(tmp_path / "s.md", "---\ndescription: d\n---\nbody")
        skill, _ = load_skill_from_file(f)
        assert skill is not None
        assert skill.file_path == str(f)

    def test_nonexistent_file_returns_error(self, tmp_path):
        _, err = load_skill_from_file(tmp_path / "ghost.md")
        assert err is not None


class TestLoadSkillsFromDir:
    def _valid_md(self, content="body") -> str:
        return f"---\ndescription: a skill\n---\n{content}"

    def test_empty_dir_returns_empty_result(self, tmp_path):
        result = load_skills_from_dir(tmp_path)
        assert result.skills == {}
        assert result.errors == []

    def test_missing_dir_returns_empty(self, tmp_path):
        result = load_skills_from_dir(tmp_path / "nonexistent")
        assert result.skills == {}

    def test_loads_root_md_files(self, tmp_path):
        _write(tmp_path / "skill_a.md", self._valid_md("do A"))
        _write(tmp_path / "skill_b.md", self._valid_md("do B"))
        result = load_skills_from_dir(tmp_path)
        assert "skill_a" in result.skills
        assert "skill_b" in result.skills

    def test_loads_skill_from_subdir_skill_md(self, tmp_path):
        sub = tmp_path / "my-skill"
        sub.mkdir()
        _write(sub / "SKILL.md", self._valid_md("skill content"))
        result = load_skills_from_dir(tmp_path)
        assert "my-skill" in result.skills

    def test_error_collected_for_bad_file(self, tmp_path):
        _write(tmp_path / "bad.md", "---\nno_description: here\n---\nbody")
        result = load_skills_from_dir(tmp_path)
        assert len(result.errors) >= 1

    def test_non_md_ignored(self, tmp_path):
        _write(tmp_path / "readme.txt", "ignore me")
        _write(tmp_path / "config.json", "{}")
        _write(tmp_path / "skill.md", self._valid_md())
        result = load_skills_from_dir(tmp_path)
        assert len(result.skills) == 1

    def test_skill_md_takes_priority_over_root_scan(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        _write(sub / "SKILL.md", self._valid_md("from subdir"))
        _write(sub / "extra.md", self._valid_md("this should be ignored"))
        result = load_skills_from_dir(tmp_path)
        assert "nested" in result.skills
        assert "extra" not in result.skills
