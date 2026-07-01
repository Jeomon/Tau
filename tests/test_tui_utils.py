"""Tests for tau/tui/utils.py — project_name."""

from __future__ import annotations

from tau.tui.utils import project_name


class TestProjectName:
    def test_returns_string(self):
        result = project_name()
        assert isinstance(result, str)

    def test_nonempty(self):
        assert len(project_name()) > 0

    def test_returns_cwd_name_when_no_git(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No .git directory — subprocess will fail or return non-zero
        result = project_name()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_in_git_repo_returns_basename(self, monkeypatch):
        import subprocess

        class _GitResult:
            returncode = 0
            stdout = "/home/user/my-project\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _GitResult())
        result = project_name()
        assert result == "my-project"
        assert "/" not in result
        assert "\\" not in result
