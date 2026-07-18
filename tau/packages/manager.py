from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from tau.packages.utils import extensions_from_pyproject, parse_source
from tau.settings.paths import get_app_name
from tau.settings.types import PackageEntry

_log = logging.getLogger(__name__)

# None of these subprocess.run() calls had a timeout — a hung pip/uv (a
# private index prompting for credentials with no TTY to answer on, a stalled
# network) blocked forever with no recovery short of killing the process.
# install_requirements() runs on the extension-loading path (via
# ExtensionLoader._load_one() -> asyncio.to_thread), awaited inside an
# asyncio.gather() over every discovered extension — one hung install there
# means the whole gather() never completes, so app startup or a full
# extension reload hangs indefinitely, not just that one extension's load.
_INSTALL_TIMEOUT_SECONDS = 120
# Local, network-free introspection of an already-installed venv (site
# location, an installed package's version) — a genuine hang here would be
# pathological, not a legitimately slow operation, so this is tighter.
_QUERY_TIMEOUT_SECONDS = 15


def _package_path(root: Path, declared: object, *, file_only: bool = False) -> Path | None:
    """Resolve a package declaration without permitting path traversal/symlink escape."""
    if not isinstance(declared, str):
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root / declared).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        _log.warning("ignoring package declaration outside %s: %r", resolved_root, declared)
        return None
    if not candidate.exists() or (file_only and not candidate.is_file()):
        return None
    return candidate


class PackageManager:
    """Manages Python extension packages in a dedicated venv."""

    def __init__(self, venv_dir: Path, *, python_executable: Path | None = None) -> None:
        self.venv_dir = venv_dir
        # When set, target this existing interpreter directly instead of managing
        # a venv under venv_dir — used when venv_dir is the running interpreter's
        # own (non-venv) installation, e.g. a system Python framework build.
        self._python_override = python_executable

    # ── Venv paths ────────────────────────────────────────────────────────────

    @property
    def _python(self) -> Path:
        """Return the path to the target Python executable."""
        if self._python_override is not None:
            return self._python_override
        if sys.platform == "win32":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    @property
    def _pip_exe(self) -> Path:
        """Return the path to the venv's pip executable."""
        if sys.platform == "win32":
            return self.venv_dir / "Scripts" / "pip.exe"
        return self.venv_dir / "bin" / "pip"

    def _has_uv(self) -> bool:
        """Check if uv package manager is installed."""
        return shutil.which("uv") is not None

    def ensure_venv(self) -> None:
        """Create the venv if it does not already exist."""
        if self._python_override is not None:
            return  # targeting an existing interpreter directly; nothing to create
        if self._python.exists():
            return
        self.venv_dir.mkdir(parents=True, exist_ok=True)
        if self._has_uv():
            # Without --python, uv picks its own default toolchain (which can be a
            # different version than the interpreter actually running Tau), producing
            # a venv with import-incompatible native extensions. Pin explicitly.
            subprocess.run(
                ["uv", "venv", "--python", sys.executable, str(self.venv_dir)],
                check=True,
                capture_output=True,
                timeout=_INSTALL_TIMEOUT_SECONDS,
            )
        else:
            subprocess.run(
                [sys.executable, "-m", "venv", str(self.venv_dir)],
                check=True,
                capture_output=True,
                timeout=_INSTALL_TIMEOUT_SECONDS,
            )

    def site_packages(self) -> Path | None:
        """Return the venv's site-packages directory."""
        if not self._python.exists():
            return None
        # No check=True — this has always tolerated failure by returning None
        # rather than raising (callers, e.g. resource discovery, aren't
        # wrapped in a broad try/except here). A timeout must fail the same
        # way, not surface as a new, previously-impossible crash.
        try:
            result = subprocess.run(
                [str(self._python), "-c", "import site; print(site.getsitepackages()[0])"],
                capture_output=True,
                text=True,
                timeout=_QUERY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
        return None

    # ── Package operations ────────────────────────────────────────────────────

    def install(
        self,
        source: str,
        *,
        index_url: str | None = None,
        extra_index_urls: list[str] | None = None,
    ) -> PackageEntry:
        """Install a package and return a PackageEntry with metadata."""
        parsed = parse_source(source)
        self.ensure_venv()

        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), parsed.install_spec or ""]
        else:
            cmd = [str(self._pip_exe), "install", parsed.install_spec or ""]
        if index_url:
            cmd.extend(["--index-url", index_url])
        for url in extra_index_urls or []:
            cmd.extend(["--extra-index-url", url])
        subprocess.run(cmd, check=True, timeout=_INSTALL_TIMEOUT_SECONDS)

        installed_path = self._find_package_dir(parsed.name)
        version = parsed.version or self._get_installed_version(parsed.name)

        return PackageEntry(
            source=source,
            name=parsed.name,
            version=version,
            installed_path=str(installed_path) if installed_path else None,
            index_url=index_url,
            extra_index_urls=extra_index_urls,
        )

    def remove(self, name: str) -> None:
        """Uninstall a package from the venv."""
        if self._has_uv():
            cmd = ["uv", "pip", "uninstall", "--python", str(self._python), name]
        else:
            cmd = [str(self._pip_exe), "uninstall", "-y", name]
        subprocess.run(cmd, check=True, timeout=_INSTALL_TIMEOUT_SECONDS)

    def install_requirements(self, dependencies: list[str]) -> None:
        """Install a batch of dependency specs (e.g. extension-declared requirements)."""
        if not dependencies:
            return
        self.ensure_venv()
        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), *dependencies]
        else:
            cmd = [str(self._python), "-m", "pip", "install", *dependencies]
        subprocess.run(cmd, check=True, capture_output=True, timeout=_INSTALL_TIMEOUT_SECONDS)

    def update(
        self,
        name: str,
        *,
        index_url: str | None = None,
        extra_index_urls: list[str] | None = None,
    ) -> str | None:
        """Upgrade a package to the latest version and return the new version string."""
        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), "--upgrade", name]
        else:
            cmd = [str(self._pip_exe), "install", "--upgrade", name]
        if index_url:
            cmd.extend(["--index-url", index_url])
        for url in extra_index_urls or []:
            cmd.extend(["--extra-index-url", url])
        subprocess.run(cmd, check=True, timeout=_INSTALL_TIMEOUT_SECONDS)
        return self._get_installed_version(name)

    # ── Extension discovery ───────────────────────────────────────────────────

    def find_extension_files(self, name: str, installed_path: str | None = None) -> list[Path]:
        """Return the extension .py files for an installed package.

        Discovery order:
          1. manifest.json with {get_app_name_lower(): {"extensions": [...]}}
          2. pyproject.toml with [tool.{get_app_name_lower()}] extensions list
          3. __init__.py that defines register()
        """
        pkg_dir = Path(installed_path) if installed_path else self._find_package_dir(name)

        if not pkg_dir or not pkg_dir.is_dir():
            return []

        # 1. manifest.json
        manifest = pkg_dir / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                section = data.get(get_app_name().lower(), {}) if isinstance(data, dict) else {}
                declared = section.get("extensions", []) if isinstance(section, dict) else []
                if isinstance(declared, list):
                    paths = [_package_path(pkg_dir, value, file_only=True) for value in declared]
                    found = [path for path in paths if path is not None]
                    if found:
                        return found
            except (json.JSONDecodeError, OSError):
                _log.warning("failed to parse package manifest %s", manifest, exc_info=True)

        # 2. pyproject.toml (package dir or its parent)
        for pp in [pkg_dir / "pyproject.toml", pkg_dir.parent / "pyproject.toml"]:
            if pp.is_file():
                found = extensions_from_pyproject(pp, pp.parent)
                if found:
                    return found

        # 3. __init__.py with a register() function
        init = pkg_dir / "__init__.py"
        if init.is_file():
            try:
                content = init.read_text(encoding="utf-8")
                if "def register(" in content or "async def register(" in content:
                    return [init.resolve()]
            except OSError:
                _log.warning("failed to read package __init__.py %s", init, exc_info=True)

        return []

    def find_resource_paths(
        self,
        name: str,
        resource: str,
        installed_path: str | None = None,
        include: list[str] | None = None,
    ) -> list[Path]:
        """Return declared package resource paths after applying an optional filter."""
        if resource not in {"extensions", "skills", "prompts", "themes"}:
            raise ValueError(f"Unsupported package resource: {resource}")
        pkg_dir = Path(installed_path) if installed_path else self._find_package_dir(name)
        if not pkg_dir or not pkg_dir.is_dir() or include == []:
            return []

        declared: list[str] = []
        manifest = pkg_dir / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                section = data.get(get_app_name().lower(), {}) if isinstance(data, dict) else {}
                value = section.get(resource, []) if isinstance(section, dict) else []
                declared = value if isinstance(value, list) else []
            except (json.JSONDecodeError, OSError):
                _log.warning("failed to parse package manifest %s", manifest, exc_info=True)

        if not declared:
            conventional = pkg_dir / resource
            if conventional.exists():
                declared = [f"./{resource}"]

        paths = [_package_path(pkg_dir, value) for value in declared]
        paths = [path for path in paths if path is not None]
        if include is None:
            return paths

        selected: list[Path] = []
        for path in paths:
            relative = str(path.relative_to(pkg_dir))
            if relative in include or path.name in include or f"./{relative}" in include:
                selected.append(path)
        return selected

    def is_installed(self, name: str) -> bool:
        """Return True if a package with this name is importable in the venv."""
        return self._get_installed_version(name) is not None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_installed_version(self, name: str) -> str | None:
        """Query the installed version of a package."""
        if not self._python.exists():
            return None
        for n in [name.replace("-", "_").lower(), name.lower()]:
            # No check=True — this has always tolerated failure by trying the
            # next name variant / falling through to None rather than
            # raising. A timeout must fail the same way, not surface as a
            # new, previously-impossible crash.
            try:
                result = subprocess.run(
                    [
                        str(self._python),
                        "-c",
                        f"import importlib.metadata; print(importlib.metadata.version({n!r}))",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=_QUERY_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return None

    def _find_package_dir(self, name: str) -> Path | None:
        """Locate the installation directory of a package in site-packages."""
        site_pkgs = self.site_packages()
        if not site_pkgs or not site_pkgs.is_dir():
            return None
        normalized = name.replace("-", "_")
        for candidate in [name, normalized, normalized.lower(), normalized.upper()]:
            p = site_pkgs / candidate
            if p.is_dir():
                return p
        return None
