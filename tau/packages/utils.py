from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)

from tau.packages.types import ParsedSource, SourceType
from tau.settings.paths import get_app_name


def add_site_packages_path(path: Path | None) -> None:
    """Add an extension package directory without shadowing Tau dependencies."""
    if path is None:
        return
    value = str(path)
    if value not in sys.path:
        sys.path.append(value)


def redact_source(source: str) -> str:
    """Remove URL userinfo and sensitive query/fragment data before display."""
    return re.sub(r"(?<=//)[^/@\s]+@", "", source.split("?", 1)[0].split("#", 1)[0])


def parse_source(source: str) -> ParsedSource:
    """Parse a package source string into its components.

    Supported formats:
      pypi:package-name
      pypi:package-name==1.0.0
      git+https://github.com/user/repo
      git+https://github.com/user/repo@v1
      https://example.com/package-1.0.0-py3-none-any.whl
      /absolute/path  or  ./relative/path  or  ~/path
      bare-name  (treated as pypi)
    """
    s = source.strip()

    if s.startswith("pypi:"):
        rest = s[5:]
        if "==" in rest:
            name, _, version = rest.partition("==")
            if not version:
                raise ValueError(f"Cannot parse package source: {source!r}")
        else:
            name, version = rest, None
        name = name.strip()
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
            raise ValueError(f"Cannot parse package source: {source!r}")
        normalized_name = canonicalize_name(name)
        spec = f"{name}=={version}" if version else name
        return ParsedSource(
            source=SourceType.PYPI,
            raw=source,
            name=normalized_name,
            version=version,
            install_spec=spec,
        )

    if s.startswith("git+"):
        # A Git revision may contain slashes. Only .git@ starts the revision;
        # stripping at a slash would make the branch name the package name.
        repository = (
            s.split(".git@", 1)[0] + ".git"
            if ".git@" in s
            else s.rsplit("@", 1)[0]
            if "@" in s
            else s
        )
        name = re.sub(r"\.git$", "", repository).rstrip("/").split("/")[-1]
        return ParsedSource(
            source=SourceType.GIT,
            raw=source,
            name=canonicalize_name(name),
            install_spec=source,
        )

    if s.startswith(("https://", "http://")):
        filename = Path(unquote(urlparse(s).path)).name
        name, version = _distribution_name_and_version(filename)
        return ParsedSource(
            source=SourceType.URL,
            raw=source,
            name=name,
            version=version,
            install_spec=s,
        )

    if s.startswith(("/", ".", "~")):
        path = Path(s).expanduser().resolve()
        name, version = _distribution_name_and_version(path.name, fallback=path.name)
        return ParsedSource(
            source=SourceType.LOCAL,
            raw=source,
            name=name,
            version=version,
            install_spec=str(path),
        )

    # Bare name — treat as pypi
    m = re.match(r"^([a-zA-Z0-9_.-]+)(?:==(.+))?$", s)
    if m:
        name, version = m.group(1), m.group(2)
        spec = f"{name}=={version}" if version else name
        return ParsedSource(
            source=SourceType.PYPI,
            raw=source,
            name=canonicalize_name(name),
            version=version,
            install_spec=spec,
        )

    raise ValueError(f"Cannot parse package source: {source!r}")



def _distribution_name_and_version(
    filename: str, *, fallback: str | None = None
) -> tuple[str, str | None]:
    """Extract normalized distribution metadata from a wheel or sdist filename."""
    try:
        name, version, _build, _tags = parse_wheel_filename(filename)
        return str(name), str(version)
    except InvalidWheelFilename:
        pass
    try:
        name, version = parse_sdist_filename(filename)
        return str(name), str(version)
    except InvalidSdistFilename:
        if fallback is not None:
            return canonicalize_name(fallback), None
        raise ValueError(f"URL does not identify a wheel or source archive: {filename!r}") from None


def extensions_from_pyproject(pyproject: Path, base: Path) -> list[Path]:
    """Read [tool.tau].extensions, rejecting declarations outside its root."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        section = (
            data.get("tool", {}).get(get_app_name().lower(), {})
            if isinstance(data, dict)
            else {}
        )
        declared = section.get("extensions", []) if isinstance(section, dict) else []
        root = base.resolve()
        paths: list[Path] = []
        for value in declared if isinstance(declared, list) else []:
            if not isinstance(value, str):
                continue
            candidate = (root / value).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                paths.append(candidate)
        return paths
    except Exception:
        return []
