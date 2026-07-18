"""Tests for tau/trust/utils.py — trust path resolution and option building."""

from __future__ import annotations

from pathlib import Path

from tau.trust.types import TrustOption
from tau.trust.utils import find_nearest, get_trust_options, normalize


class TestNormalize:
    def test_returns_string(self, tmp_path):
        result = normalize(str(tmp_path))
        assert isinstance(result, str)

    def test_resolves_to_absolute(self):
        result = normalize(".")
        assert Path(result).is_absolute()

    def test_path_object_accepted(self, tmp_path):
        result = normalize(tmp_path)
        assert result == str(tmp_path.resolve())


class TestFindNearest:
    def test_exact_path_trusted(self, tmp_path):
        cwd = str(tmp_path)
        data: dict[str, bool | None] = {cwd: True}
        result = find_nearest(data, cwd)
        assert result == (cwd, True)

    def test_exact_path_untrusted(self, tmp_path):
        cwd = str(tmp_path)
        data: dict[str, bool | None] = {cwd: False}
        result = find_nearest(data, cwd)
        assert result == (cwd, False)

    def test_parent_trusted_when_child_missing(self, tmp_path):
        child = tmp_path / "project"
        child.mkdir()
        parent = str(tmp_path.resolve())
        data: dict[str, bool | None] = {parent: True}
        result = find_nearest(data, str(child))
        assert result == (parent, True)

    def test_returns_none_when_no_entry(self, tmp_path):
        result = find_nearest({}, str(tmp_path))
        assert result is None

    def test_child_overrides_parent(self, tmp_path):
        child = tmp_path / "sub"
        child.mkdir()
        parent_path = str(tmp_path.resolve())
        child_path = str(child.resolve())
        data: dict[str, bool | None] = {parent_path: True, child_path: False}
        result = find_nearest(data, child_path)
        assert result == (child_path, False)


class TestGetTrustOptions:
    def test_returns_list_of_trust_options(self, tmp_path):
        options = get_trust_options(str(tmp_path))
        assert all(isinstance(o, TrustOption) for o in options)

    def test_always_has_trust_option(self, tmp_path):
        options = get_trust_options(str(tmp_path))
        labels = [o.label for o in options]
        assert "Trust" in labels

    def test_always_has_do_not_trust(self, tmp_path):
        options = get_trust_options(str(tmp_path))
        labels = [o.label for o in options]
        assert "Do not trust" in labels

    def test_session_only_option_included_by_default(self, tmp_path):
        options = get_trust_options(str(tmp_path))
        labels = [o.label for o in options]
        assert "Trust (this session only)" in labels

    def test_session_only_option_excluded(self, tmp_path):
        options = get_trust_options(str(tmp_path), session_only=False)
        labels = [o.label for o in options]
        assert "Trust (this session only)" not in labels

    def test_parent_option_included(self, tmp_path):
        child = tmp_path / "project"
        child.mkdir()
        options = get_trust_options(str(child))
        labels = [o.label for o in options]
        assert any("parent folder" in label.lower() for label in labels)

    def test_trust_option_save_path_is_resolved(self, tmp_path):
        options = get_trust_options(str(tmp_path))
        trust_opt = next(o for o in options if o.label == "Trust")
        assert trust_opt.save_path is not None
        assert Path(trust_opt.save_path).is_absolute()


class TestHasProjectTrustInputs:
    def test_false_for_empty_dir(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        assert has_project_trust_inputs(tmp_path) is False

    def test_true_when_tau_config_dir_exists(self, tmp_path):
        from tau.settings.paths import CONFIG_DIR_NAME
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / CONFIG_DIR_NAME).mkdir()
        assert has_project_trust_inputs(tmp_path) is True

    def test_true_when_agents_skills_dir_exists(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        assert has_project_trust_inputs(tmp_path) is True

    def test_true_when_in_ancestor_dir(self, tmp_path):
        from tau.settings.paths import CONFIG_DIR_NAME
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / CONFIG_DIR_NAME).mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert has_project_trust_inputs(nested) is True

    def test_accepts_string_path(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        assert has_project_trust_inputs(str(tmp_path)) is False

    def test_true_when_agents_md_in_cwd(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
        assert has_project_trust_inputs(tmp_path) is True

    def test_true_when_claude_md_in_cwd(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / "CLAUDE.md").write_text("instructions", encoding="utf-8")
        assert has_project_trust_inputs(tmp_path) is True

    def test_context_file_match_is_case_insensitive(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        (tmp_path / "agents.md").write_text("instructions", encoding="utf-8")
        assert has_project_trust_inputs(tmp_path) is True

    def test_true_when_context_file_at_git_root(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("instructions", encoding="utf-8")
        nested = repo / "a" / "b"
        nested.mkdir(parents=True)
        assert has_project_trust_inputs(nested) is True

    def test_context_file_above_git_root_ignored(self, tmp_path):
        from tau.trust.utils import has_project_trust_inputs

        # The loader only injects context files from the git root down, so a
        # file above the repo is not a trust input.
        (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert has_project_trust_inputs(repo) is False

    def test_global_config_dir_is_not_a_project_trust_input(self, tmp_path, monkeypatch):
        import tau.trust.utils as trust_utils

        fake_home = tmp_path / "home"
        global_config = fake_home / ".tau"
        global_config.mkdir(parents=True)
        monkeypatch.setattr(trust_utils, "CONFIG_DIR_PATH", global_config)
        project = fake_home / "project"
        project.mkdir()
        # Walking up from a cwd under $HOME finds ~/.tau, but the user's own
        # global config dir must not trigger the project trust prompt.
        assert trust_utils.has_project_trust_inputs(project) is False

    def test_project_local_tau_dir_still_counts_under_home(self, tmp_path, monkeypatch):
        import tau.trust.utils as trust_utils

        fake_home = tmp_path / "home"
        global_config = fake_home / ".tau"
        global_config.mkdir(parents=True)
        monkeypatch.setattr(trust_utils, "CONFIG_DIR_PATH", global_config)
        project = fake_home / "project"
        (project / ".tau").mkdir(parents=True)
        assert trust_utils.has_project_trust_inputs(project) is True
