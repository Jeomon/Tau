"""Configuration directory paths for Tau application.

Tau stores all user configuration and session data in ~/.tau/ (global)
and .tau/ (project-level). This module provides functions to get the
correct paths for different data types.

Global paths: ~/.tau/
  - settings.json: user preferences
  - auth.json: authentication credentials
  - sessions/: persistent session data
  - prompts/, tools/, skills/, commands/: custom extensions
  - themes/: custom UI themes
  - hooks/: lifecycle hooks

Project paths: .tau/
  - settings.json: project-specific overrides
  - extensions/: project-local extensions
"""

from pathlib import Path

APP_NAME = "Tau"
PACKAGE_NAME = "tau-coding-agent"
CONFIG_DIR_NAME = ".tau"

CONFIG_DIR_PATH = Path.home() / CONFIG_DIR_NAME


# ── Centralized app name helper ────────────────────────────────────────────────


def get_app_name() -> str:
    """Return the application name."""
    return APP_NAME


def get_package_name() -> str:
    """Return the PyPI distribution package name."""
    return PACKAGE_NAME


def get_app_version() -> str:
    """Return the installed package version, falling back to '0.1.0'."""
    try:
        from importlib.metadata import version

        return version(get_package_name())
    except Exception:
        return "0.1.0"


def get_config_dir(cwd: Path | None = None) -> Path:
    if cwd is not None and cwd.exists():
        return cwd / CONFIG_DIR_NAME
    return CONFIG_DIR_PATH


# ── User-facing files ────────────────────────────────────────────────────────


def get_settings_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "settings.json"


def get_auth_path() -> Path:
    return CONFIG_DIR_PATH / "auth.json"


def get_system_prompt_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "SYSTEM.md"


def get_append_system_prompt_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "APPEND_SYSTEM.md"


# ── Runtime dirs (all flat under .tau/) ───────────────────────────────────────


def get_sessions_dir() -> Path:
    return CONFIG_DIR_PATH / "sessions"


def get_logs_dir(cwd: Path | None = None) -> Path:
    "Path to a per-run logs directory, named by the run/session id."
    return get_config_dir(cwd) / "logs"


def get_themes_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "themes"


def get_extensions_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "extensions"


def get_prompts_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "prompts"


def get_tools_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "tools"


def get_skills_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "skills"


def get_commands_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "commands"


def get_hooks_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "hooks"


def get_temp_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "temp"


def get_packages_venv(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "venv"


def get_builtins_dir() -> Path:
    return Path(__file__).parent.parent / "builtins"


def get_docs_dir() -> Path:
    """Get the docs directory path.

    Works both when tau is installed via pip and when running from source.
    """
    try:
        from importlib.resources import files

        docs_ref = files("tau").joinpath("docs")
        return Path(str(docs_ref))
    except (TypeError, ModuleNotFoundError, AttributeError):
        package_root = Path(__file__).parent.parent.parent
        return package_root / "docs"


def get_readme_path() -> Path:
    """Get the README.md path.

    Works both when tau is installed via pip and when running from source.
    """
    try:
        from importlib.resources import files

        readme_ref = files("tau").joinpath("README.md")
        return Path(str(readme_ref))
    except (TypeError, ModuleNotFoundError, AttributeError):
        package_root = Path(__file__).parent.parent.parent
        return package_root / "README.md"


def get_examples_path() -> Path:
    """Get the examples directory path.

    Works both when tau is installed via pip and when running from source.
    """
    try:
        from importlib.resources import files

        examples_ref = files("tau").joinpath("examples")
        return Path(str(examples_ref))
    except (TypeError, ModuleNotFoundError, AttributeError):
        package_root = Path(__file__).parent.parent.parent
        return package_root / "examples"
