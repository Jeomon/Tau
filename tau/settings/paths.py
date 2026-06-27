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
    """Return the application name.
    
    Returns:
        str: The application name 'Tau'
    """
    return APP_NAME


def get_package_name() -> str:
    """Return the PyPI distribution package name.
    
    Returns:
        str: The package name 'tau-coding-agent'
    """
    return PACKAGE_NAME


def get_app_version() -> str:
    """Return the installed package version, falling back to '0.1.0'.
    
    Attempts to read the version from package metadata. If that fails
    (e.g., when running from source), returns a default version.
    
    Returns:
        str: The package version string
    """
    try:
        from importlib.metadata import version

        return version(get_package_name())
    except Exception:
        return "0.1.0"


def get_config_dir(cwd: Path | None = None) -> Path:
    """Get the configuration directory path for Tau.
    
    Returns the project-specific .tau/ directory if cwd exists and is valid,
    otherwise returns the global ~/.tau/ directory.
    
    Args:
        cwd: Optional current working directory. If provided and exists,
            returns project-level config dir. Otherwise returns global config dir.
    
    Returns:
        Path: Path to the configuration directory
    """
    if cwd is not None and cwd.exists():
        return cwd / CONFIG_DIR_NAME
    return CONFIG_DIR_PATH


# ── User-facing files ────────────────────────────────────────────────────────


def get_settings_path(cwd: Path | None = None) -> Path:
    """Get the path to the settings.json file.
    
    Returns the path to settings.json in either the project or global config directory,
    depending on whether cwd is provided and valid.
    
    Args:
        cwd: Optional current working directory for project-level settings
    
    Returns:
        Path: Path to settings.json file
    """
    return get_config_dir(cwd) / "settings.json"


def get_auth_path() -> Path:
    """Get the path to the global auth.json file.
    
    Returns:
        Path: Path to auth.json file in global config directory
    """
    return CONFIG_DIR_PATH / "auth.json"


def get_system_prompt_path(cwd: Path | None = None) -> Path:
    """Get the path to the SYSTEM.md file.
    
    Returns the path to SYSTEM.md in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level system prompt
    
    Returns:
        Path: Path to SYSTEM.md file
    """
    return get_config_dir(cwd) / "SYSTEM.md"


def get_append_system_prompt_path(cwd: Path | None = None) -> Path:
    """Get the path to the APPEND_SYSTEM.md file.
    
    Returns the path to APPEND_SYSTEM.md in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level append system prompt
    
    Returns:
        Path: Path to APPEND_SYSTEM.md file
    """
    return get_config_dir(cwd) / "APPEND_SYSTEM.md"


# ── Runtime dirs (all flat under .tau/) ───────────────────────────────────────


def get_sessions_dir() -> Path:
    """Get the path to the global sessions directory.
    
    Sessions are always stored in the global config directory, not project-specific.
    
    Returns:
        Path: Path to sessions directory
    """
    return CONFIG_DIR_PATH / "sessions"


def get_logs_dir(cwd: Path | None = None) -> Path:
    """Get the path to the logs directory.
    
    Returns the path to logs directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level logs
    
    Returns:
        Path: Path to logs directory
    """
    return get_config_dir(cwd) / "logs"


def get_themes_dir(cwd: Path | None = None) -> Path:
    """Get the path to the themes directory.
    
    Returns the path to themes directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level themes
    
    Returns:
        Path: Path to themes directory
    """
    return get_config_dir(cwd) / "themes"


def get_extensions_dir(cwd: Path | None = None) -> Path:
    """Get the path to the extensions directory.
    
    Returns the path to extensions directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level extensions
    
    Returns:
        Path: Path to extensions directory
    """
    return get_config_dir(cwd) / "extensions"


def get_prompts_dir(cwd: Path | None = None) -> Path:
    """Get the path to the prompts directory.
    
    Returns the path to prompts directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level prompts
    
    Returns:
        Path: Path to prompts directory
    """
    return get_config_dir(cwd) / "prompts"


def get_tools_dir(cwd: Path | None = None) -> Path:
    """Get the path to the tools directory.
    
    Returns the path to tools directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level tools
    
    Returns:
        Path: Path to tools directory
    """
    return get_config_dir(cwd) / "tools"


def get_skills_dir(cwd: Path | None = None) -> Path:
    """Get the path to the skills directory.
    
    Returns the path to skills directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level skills
    
    Returns:
        Path: Path to skills directory
    """
    return get_config_dir(cwd) / "skills"


def get_commands_dir(cwd: Path | None = None) -> Path:
    """Get the path to the commands directory.
    
    Returns the path to commands directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level commands
    
    Returns:
        Path: Path to commands directory
    """
    return get_config_dir(cwd) / "commands"


def get_hooks_dir(cwd: Path | None = None) -> Path:
    """Get the path to the hooks directory.
    
    Returns the path to hooks directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level hooks
    
    Returns:
        Path: Path to hooks directory
    """
    return get_config_dir(cwd) / "hooks"


def get_temp_dir(cwd: Path | None = None) -> Path:
    """Get the path to the temp directory.
    
    Returns the path to temp directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level temp files
    
    Returns:
        Path: Path to temp directory
    """
    return get_config_dir(cwd) / "temp"


def get_packages_venv(cwd: Path | None = None) -> Path:
    """Get the path to the packages virtual environment directory.
    
    Returns the path to venv directory in either project or global config directory.
    
    Args:
        cwd: Optional current working directory for project-level venv
    
    Returns:
        Path: Path to venv directory
    """
    return get_config_dir(cwd) / "venv"


def get_builtins_dir() -> Path:
    """Get the path to the builtins directory.
    
    Returns the path to the builtins directory within the Tau package.
    
    Returns:
        Path: Path to builtins directory
    """
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
