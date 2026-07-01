"""Tests for tau/settings/paths.py — configuration path functions."""

from __future__ import annotations

from pathlib import Path

from tau.settings.paths import (
    CONFIG_DIR_NAME,
    get_app_name,
    get_app_version,
    get_append_system_prompt_path,
    get_auth_path,
    get_builtins_dir,
    get_commands_dir,
    get_config_dir,
    get_extensions_dir,
    get_hooks_dir,
    get_logs_dir,
    get_package_name,
    get_packages_venv,
    get_prompts_dir,
    get_sessions_dir,
    get_settings_path,
    get_skills_dir,
    get_system_prompt_path,
    get_temp_dir,
    get_themes_dir,
    get_tools_dir,
)


class TestAppMeta:
    def test_app_name(self):
        assert get_app_name() == "Tau"

    def test_package_name(self):
        assert get_package_name() == "tau-coding-agent"

    def test_app_version_is_string(self):
        v = get_app_version()
        assert isinstance(v, str)
        assert len(v) > 0

    def test_app_version_has_dots(self):
        v = get_app_version()
        assert "." in v


class TestConfigDir:
    def test_no_cwd_uses_home(self):
        result = get_config_dir()
        assert result == Path.home() / CONFIG_DIR_NAME

    def test_cwd_appends_config_dir(self, tmp_path):
        result = get_config_dir(cwd=tmp_path)
        assert result == tmp_path / CONFIG_DIR_NAME

    def test_nonexistent_cwd_uses_global(self, tmp_path):
        fake = tmp_path / "nonexistent"
        result = get_config_dir(cwd=fake)
        assert result == Path.home() / CONFIG_DIR_NAME


class TestSettingsPath:
    def test_returns_settings_json(self):
        p = get_settings_path()
        assert p.name == "settings.json"

    def test_with_cwd(self, tmp_path):
        p = get_settings_path(cwd=tmp_path)
        assert p.parent == tmp_path / CONFIG_DIR_NAME
        assert p.name == "settings.json"


class TestAuthPath:
    def test_returns_auth_json(self):
        p = get_auth_path()
        assert p.name == "auth.json"

    def test_is_under_home(self):
        p = get_auth_path()
        assert p.parent == Path.home() / CONFIG_DIR_NAME


class TestSystemPromptPath:
    def test_returns_system_md(self):
        p = get_system_prompt_path()
        assert p.name == "SYSTEM.md"

    def test_append_returns_append_system_md(self):
        p = get_append_system_prompt_path()
        assert p.name == "APPEND_SYSTEM.md"


class TestRuntimeDirs:
    def test_sessions_dir_under_home_config(self):
        p = get_sessions_dir()
        assert p.name == "sessions"
        assert p.parent == Path.home() / CONFIG_DIR_NAME

    def test_logs_dir(self, tmp_path):
        p = get_logs_dir(cwd=tmp_path)
        assert p.name == "logs"

    def test_themes_dir(self, tmp_path):
        p = get_themes_dir(cwd=tmp_path)
        assert p.name == "themes"

    def test_extensions_dir(self, tmp_path):
        p = get_extensions_dir(cwd=tmp_path)
        assert p.name == "extensions"

    def test_prompts_dir(self, tmp_path):
        p = get_prompts_dir(cwd=tmp_path)
        assert p.name == "prompts"

    def test_tools_dir(self, tmp_path):
        p = get_tools_dir(cwd=tmp_path)
        assert p.name == "tools"

    def test_skills_dir(self, tmp_path):
        p = get_skills_dir(cwd=tmp_path)
        assert p.name == "skills"

    def test_commands_dir(self, tmp_path):
        p = get_commands_dir(cwd=tmp_path)
        assert p.name == "commands"

    def test_hooks_dir(self, tmp_path):
        p = get_hooks_dir(cwd=tmp_path)
        assert p.name == "hooks"

    def test_temp_dir(self, tmp_path):
        p = get_temp_dir(cwd=tmp_path)
        assert p.name == "temp"

    def test_packages_venv(self, tmp_path):
        p = get_packages_venv(cwd=tmp_path)
        assert p.name == "venv"


class TestBuiltinsDir:
    def test_builtins_dir_exists(self):
        p = get_builtins_dir()
        assert p.exists()
        assert p.is_dir()
        assert p.name == "builtins"


class TestGetDocsDirReadmePath:
    def test_get_docs_dir_returns_path(self):
        from tau.settings.paths import get_docs_dir

        p = get_docs_dir()
        assert isinstance(p, Path)

    def test_get_docs_dir_name(self):
        from tau.settings.paths import get_docs_dir

        assert get_docs_dir().name == "docs"

    def test_get_readme_path_returns_path(self):
        from tau.settings.paths import get_readme_path

        p = get_readme_path()
        assert isinstance(p, Path)

    def test_get_readme_path_name(self):
        from tau.settings.paths import get_readme_path

        assert get_readme_path().name == "README.md"

    def test_get_examples_path_returns_path(self):
        from tau.settings.paths import get_examples_path

        p = get_examples_path()
        assert isinstance(p, Path)

    def test_get_examples_path_name(self):
        from tau.settings.paths import get_examples_path

        assert get_examples_path().name == "examples"
