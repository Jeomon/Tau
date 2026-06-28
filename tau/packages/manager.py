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


class PackageManager:
    """Manages Python extension packages in a dedicated venv."""

    def __init__(self, venv_dir: Path) -> None:
        self.venv_dir = venv_dir

    # ── Venv paths ────────────────────────────────────────────────────────────

    @property
    def _python(self) -> Path:
        """Return the path to the venv's Python executable."""
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
        if self._python.exists():
            return
        self.venv_dir.mkdir(parents=True, exist_ok=True)
        if self._has_uv():
            subprocess.run(["uv", "venv", str(self.venv_dir)], check=True, capture_output=True)
        else:
            subprocess.run(
                [sys.executable, "-m", "venv", str(self.venv_dir)],
                check=True,
                capture_output=True,
            )

    def site_packages(self) -> Path | None:
        """Return the venv's site-packages directory."""
        if not self._python.exists():
            return None
        result = subprocess.run(
            [str(self._python), "-c", "import site; print(site.getsitepackages()[0])"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
        return None

    # ── Package operations ────────────────────────────────────────────────────

    def install(self, source: str) -> PackageEntry:
        """Install a package and return a PackageEntry with metadata."""
        parsed = parse_source(source)
        self.ensure_venv()

        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), parsed.install_spec or ""]
        else:
            cmd = [str(self._pip_exe), "install", parsed.install_spec or ""]
        subprocess.run(cmd, check=True)

        installed_path = self._find_package_dir(parsed.name)
        version = parsed.version or self._get_installed_version(parsed.name)

        return PackageEntry(
            source=source,
            name=parsed.name,
            version=version,
            installed_path=str(installed_path) if installed_path else None,
        )

    def remove(self, name: str) -> None:
        """Uninstall a package from the venv."""
        if self._has_uv():
            cmd = ["uv", "pip", "uninstall", "--python", str(self._python), name]
        else:
            cmd = [str(self._pip_exe), "uninstall", "-y", name]
        subprocess.run(cmd, check=True)

    def install_requirements(self, dependencies: list[str]) -> None:
        """Install a batch of dependency specs (e.g. extension-declared requirements)."""
        if not dependencies:
            return
        self.ensure_venv()
        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), *dependencies]
        else:
            cmd = [str(self._pip_exe), "install", *dependencies]
        subprocess.run(cmd, check=True, capture_output=True)

    def update(self, name: str) -> str | None:
        """Upgrade a package to the latest version and return the new version string."""
        if self._has_uv():
            cmd = ["uv", "pip", "install", "--python", str(self._python), "--upgrade", name]
        else:
            cmd = [str(self._pip_exe), "install", "--upgrade", name]
        subprocess.run(cmd, check=True)
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
                declared = data.get(get_app_name().lower(), {}).get("extensions", [])
                if declared:
                    return [(pkg_dir / p).resolve() for p in declared if (pkg_dir / p).is_file()]
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_installed_version(self, name: str) -> str | None:
        """Query the installed version of a package."""
        if not self._python.exists():
            return None
        for n in [name.replace("-", "_").lower(), name.lower()]:
            result = subprocess.run(
                [
                    str(self._python),
                    "-c",
                    f"import importlib.metadata; print(importlib.metadata.version({n!r}))",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return None

    def _find_package_dir(self, name: str) -> Path | None:
        """Locate the installation directory of a package in site-packages."""
        site_pkgs = self.site_packages()
        if not site_pkgs or not site_pkgs.is_dir():
            return None
        for candidate in [name, name.replace("-", "_"), name.replace("-", "_").lower(),name.replace("-", "_").upper()]:
            p = site_pkgs / candidate
            if p.is_dir():
                return p
        return None
