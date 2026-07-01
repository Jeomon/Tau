"""Tests for tau/builtins/extensions/footer/utils.py."""

from __future__ import annotations

import os

from tau.builtins.extensions.footer.utils import read_branch, shorten_home


class TestReadBranch:
    def test_reads_branch_from_git_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        assert read_branch(tmp_path) == "main"

    def test_reads_feature_branch(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/feature/my-branch\n")
        assert read_branch(tmp_path) == "feature/my-branch"

    def test_detached_head_returns_short_sha(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("abc1234def5678\n")
        assert read_branch(tmp_path) == "abc1234"

    def test_no_git_dir_returns_empty(self, tmp_path):
        sub = tmp_path / "not_a_repo"
        sub.mkdir()
        result = read_branch(sub)
        assert result == ""

    def test_searches_parent_directories(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/develop\n")
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        assert read_branch(sub) == "develop"

    def test_oserror_returns_empty(self):
        result = read_branch("/this/path/definitely/does/not/exist/anywhere")
        assert result == ""


class TestShortenHome:
    def test_exact_home_returns_tilde(self):
        home = os.path.expanduser("~")
        assert shorten_home(home) == "~"

    def test_path_under_home(self):
        home = os.path.expanduser("~")
        result = shorten_home(os.path.join(home, "projects", "tau"))
        assert result == "~/projects/tau"

    def test_unrelated_path_unchanged(self):
        path = "/usr/local/bin"
        assert shorten_home(path) == path

    def test_path_starting_with_home_prefix_but_not_subdir(self):
        home = os.path.expanduser("~")
        # e.g. home="/Users/alice" and path="/Users/alice2/file" — must NOT be shortened
        fake = home + "2/something"
        result = shorten_home(fake)
        assert result == fake

    def test_root_path_unchanged(self):
        assert shorten_home("/") == "/"
