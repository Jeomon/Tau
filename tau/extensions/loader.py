from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tau.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionError,
    ExtensionFactory,
    LoadExtensionsResult,
    _RuntimeRef,
)
from tau.packages.utils import add_site_packages_path

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM
    from tau.settings.manager import SettingsManager
    from tau.settings.types import ExtensionEntry

_log = logging.getLogger(__name__)

_ENTRY_POINT = "register"


async def load_inline_extensions(
    factories: list[ExtensionFactory],
    *,
    llm: TextLLM,
    settings: SettingsManager,
    cwd: Path,
    runtime_ref: _RuntimeRef,
) -> LoadExtensionsResult:
    """Execute in-memory extension factories through the standard extension API."""
    extensions: list[Extension] = []
    errors: list[ExtensionError] = []

    for index, factory in enumerate(factories):
        name = getattr(factory, "__name__", factory.__class__.__name__)
        path = f"inline:{name}:{index}"
        extension = Extension(path=path, source="inline")
        api = ExtensionAPI(
            extension=extension,
            llm=llm,
            settings=settings,
            cwd=cwd,
            runtime_ref=runtime_ref,
        )
        try:
            result = factory(api)
            if inspect.isawaitable(result):
                await result
            extensions.append(extension)
        except Exception:
            tb = traceback.format_exc()
            errors.append(
                ExtensionError(
                    extension_path=path,
                    event="load",
                    error=tb.strip().splitlines()[-1],
                    stack=tb,
                )
            )

    return LoadExtensionsResult(extensions=extensions, errors=errors)


def _venv_python_version(venv_dir: Path) -> str | None:
    """Return a venv's ``major.minor`` Python version from its ``pyvenv.cfg``.

    Reads the ``version`` / ``version_info`` key without spawning a subprocess.
    Returns ``None`` if the file is missing or unparseable.
    """
    cfg = venv_dir / "pyvenv.cfg"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("version", "version_info"):
            parts = value.strip().split(".")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                return f"{parts[0]}.{parts[1]}"
    return None


def _venv_matches_current(venv_dir: Path) -> bool:
    """True if ``venv_dir`` exists and targets the running interpreter's version."""
    return _venv_python_version(venv_dir) == f"{sys.version_info.major}.{sys.version_info.minor}"


class ExtensionLoader:
    """
    Discovers and loads Python extension files from configured directories.

    Discovery order (project first, then global, then explicit paths):
      - <cwd>/.tau/extensions/
      - ~/.tau/extensions/
      - Any entries in settings.extensions.list that are enabled

    Within each directory, files and subdirectories are sorted alphabetically.
    Files whose names start with ``_`` are skipped.  Files whose stem appears
    in ``disabled_stems`` are also skipped.  Duplicate resolved paths are
    skipped silently.  A failing extension is logged and recorded in the
    returned ``LoadExtensionsResult`` — it never crashes startup.

    Subdirectory support:
      Given ``extensions/my_pkg/``:
        1. ``my_pkg/manifest.json`` with ``{"tau": {"extensions": ["./main.py"]}}``
           → load the declared paths.
        2. ``my_pkg/__init__.py``
           → load as a Python package entry point.
      If neither is present the subdirectory is ignored.

    Dependency installation:
      ``manifest.json`` may also declare ``{"tau": {"dependencies": ["pkg>=1.0"]}}``.
      Before the entry file is executed, these specs are installed (once — cached by
      a hash of the dependency list) via ``uv pip install``, falling back to the
      venv's own ``pip`` when ``uv`` is not on PATH. No dedicated venv is created
      under ``.tau/``: project extensions install into the project's own ``.venv``
      when one already exists, otherwise everything falls back to the global
      packages venv at ``~/.tau/venv``. The resolved venv's site-packages directory
      is then added to ``sys.path`` so the extension's own imports resolve.
    """

    def __init__(
        self,
        project_dir: Path | None = None,
        global_dir: Path | None = None,
        extra_entries: list[ExtensionEntry] | None = None,
        extra_sources: dict[str, str] | None = None,
        disabled_stems: set[str] | None = None,
        entry_configs: dict[str, dict] | None = None,
        builtins_dir: Path | None = None,
        # Dependencies injected for per-extension ExtensionAPI creation
        llm: TextLLM | None = None,
        settings: SettingsManager | None = None,
        cwd: Path | None = None,
        runtime_ref: _RuntimeRef | None = None,
    ) -> None:
        self._builtins_dir: Path | None = (
            builtins_dir if (builtins_dir is not None and builtins_dir.is_dir()) else None
        )
        self._dirs: list[Path] = []
        self._dir_sources: dict[Path, str] = {}
        self._extra_paths: list[Path] = []
        self._extra_sources: dict[str, str] = extra_sources or {}
        self._disabled_stems: set[str] = disabled_stems or set()
        self._entry_configs: dict[str, dict] = entry_configs or {}
        self._subdir_deps: dict[Path, list[str]] = {}
        self._subdir_settings: dict[Path, dict] = {}
        self._llm = llm
        self._settings = settings
        self._cwd = cwd or Path(".")
        self._runtime_ref = runtime_ref

        # Project first, then global
        if project_dir is not None and project_dir.is_dir():
            self._dirs.append(project_dir)
            self._dir_sources[project_dir] = "project"
        if global_dir is not None and global_dir.is_dir():
            self._dirs.append(global_dir)
            self._dir_sources[global_dir] = "global"

        for entry in extra_entries or []:
            resolved = Path(entry.path).expanduser().resolve()
            if resolved.exists():
                self._extra_paths.append(resolved)
                if entry.settings:
                    self._entry_configs.setdefault(resolved.stem, entry.settings)

    # ── Discovery ──────────────────────────────────────────────────────────────

    def _subdir_entries(self, subdir: Path) -> list[Path]:
        """Return extension entry files for a subdirectory, or empty list."""
        manifest = subdir / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                from tau.settings.paths import get_app_name

                app_data = data.get(get_app_name().lower(), {})

                deps = app_data.get("dependencies", [])
                if deps:
                    self._subdir_deps[subdir.resolve()] = list(deps)

                settings_schema = app_data.get("settings")
                if isinstance(settings_schema, dict):
                    self._subdir_settings[subdir.resolve()] = settings_schema

                declared = app_data.get("extensions", [])
                if declared:
                    entries = [(subdir / p).resolve() for p in declared]
                    valid = [e for e in entries if e.is_file()]
                    if valid:
                        return valid
            except (json.JSONDecodeError, OSError):
                pass

        init = subdir / "__init__.py"
        if init.is_file():
            return [init]

        return []

    def _discover_in_dir(self, d: Path, seen: set[Path]) -> list[Path]:
        """Collect extension files from one directory (one level deep)."""
        found: list[Path] = []
        for entry in sorted(d.iterdir(), key=lambda e: e.name):
            if entry.name.startswith("_"):
                continue
            if entry.is_file() and entry.suffix == ".py":
                if entry.stem in self._disabled_stems:
                    continue
                r = entry.resolve()
                if r not in seen:
                    seen.add(r)
                    found.append(entry)
            elif entry.is_dir():
                if entry.name in self._disabled_stems:
                    continue
                for sub_entry in self._subdir_entries(entry):
                    r = sub_entry.resolve()
                    if r not in seen:
                        seen.add(r)
                        found.append(sub_entry)
        return found

    def _discover(self) -> list[tuple[Path, str]]:
        """Return deduplicated list of (extension file, source label) pairs to load."""
        found: list[tuple[Path, str]] = []
        seen: set[Path] = set()

        # Builtins always first, before any user dirs
        if self._builtins_dir is not None:
            for path in self._discover_in_dir(self._builtins_dir, seen):
                found.append((path, "builtin"))

        for d in self._dirs:
            source = self._dir_sources.get(d, "unknown")
            for path in self._discover_in_dir(d, seen):
                found.append((path, source))

        for p in self._extra_paths:
            source = self._extra_sources.get(str(p.resolve()), "explicit")
            if p.is_file() and p.suffix == ".py":
                r = p.resolve()
                if r not in seen:
                    seen.add(r)
                    found.append((p, source))
            elif p.is_dir():
                entries = self._subdir_entries(p)
                if entries:
                    for e in entries:
                        r = e.resolve()
                        if r not in seen:
                            seen.add(r)
                            found.append((e, source))
                else:
                    for path in self._discover_in_dir(p, seen):
                        found.append((path, source))

        return found

    # ── Dependency installation ──────────────────────────────────────────────────

    def _resolve_venv_dir(self, source: str) -> Path:
        """Pick a venv to install extension dependencies into.

        Project extensions prefer the project's own ``.venv`` — but only if its
        Python version matches the interpreter actually running Tau. Native
        (C-extension) dependencies are built for a specific Python version, and
        the resolved venv's site-packages is appended to *this* process's
        ``sys.path``; a mismatch makes those imports fail (e.g. numpy raising
        ``No module named 'numpy._core._multiarray_umath'``). This happens when
        Tau is installed into its own venv (``uv tool install``) while the
        project ``.venv`` was created with a different Python. In that case we
        install into the running interpreter's own environment, which is the
        only target guaranteed to be import-compatible. Everything else
        (global, explicit, unknown sources) uses ``~/.tau/venv``.
        """
        from tau.settings.paths import get_packages_venv

        if source == "project":
            project_venv = self._cwd / ".venv"
            # An existing project .venv built for a different Python version would
            # ship binaries this interpreter can't import — divert to the running
            # interpreter's own (guaranteed-compatible) environment instead.
            if project_venv.exists() and not _venv_matches_current(project_venv):
                _log.warning(
                    "Project .venv at %s targets Python %s but Tau is running on %s; "
                    "installing extension dependencies into the running interpreter "
                    "to keep native packages import-compatible.",
                    project_venv,
                    _venv_python_version(project_venv) or "unknown",
                    f"{sys.version_info.major}.{sys.version_info.minor}",
                )
                return Path(sys.prefix)
            return project_venv

        return get_packages_venv(None)

    def _ensure_dependencies(self, subdir: Path, deps: list[str], source: str) -> None:
        """Install an extension's declared dependencies, once per dependency set.

        Runs synchronously (called via ``asyncio.to_thread``) so the blocking
        subprocess install doesn't stall the event loop.
        """
        from tau.packages.manager import PackageManager

        venv_dir = self._resolve_venv_dir(source)
        pkg_mgr = PackageManager(venv_dir)

        digest = hashlib.sha256("\n".join(sorted(deps)).encode("utf-8")).hexdigest()
        cache_file = venv_dir / ".tau_ext_deps.json"
        cache: dict[str, str] = {}
        if cache_file.is_file():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _log.debug("dependency cache unreadable at %s, reinstalling", cache_file)
                cache = {}

        key = str(subdir.resolve())
        if cache.get(key) != digest:
            _log.info("Installing dependencies for extension %r: %s", subdir.name, ", ".join(deps))
            pkg_mgr.install_requirements(deps)
            cache[key] = digest
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")

        add_site_packages_path(pkg_mgr.site_packages())

    # ── Loading ────────────────────────────────────────────────────────────────

    async def load(self) -> LoadExtensionsResult:
        """Discover and load all extensions in parallel.

        Returns a ``LoadExtensionsResult`` with loaded ``Extension`` objects and
        any ``ExtensionError`` entries for files that failed to load.
        """
        discovered = self._discover()

        async def _load(path: Path, source: str) -> tuple[Extension | None, list[ExtensionError]]:
            config_key = path.parent.name if path.name == "__init__.py" else path.stem
            config = self._entry_configs.get(config_key, {})
            ext, errs = await self._load_one(path, config, source=source)
            if ext is not None:
                _log.debug("Loaded extension: %s (%s)", path, source)
            return ext, errs

        results = await asyncio.gather(*[_load(p, s) for p, s in discovered])

        extensions: list[Extension] = []
        errors: list[ExtensionError] = []
        for ext, errs in results:
            if ext is not None:
                extensions.append(ext)
            errors.extend(errs)

        return LoadExtensionsResult(extensions=extensions, errors=errors)

    async def _load_one(
        self, path: Path, config: dict, *, source: str = "unknown"
    ) -> tuple[Extension | None, list[ExtensionError]]:
        """Load a single extension file and call its ``register(tau)`` factory."""
        str_path = str(path)
        errors: list[ExtensionError] = []

        try:
            deps = self._subdir_deps.get(path.parent.resolve())
            if deps:
                await asyncio.to_thread(self._ensure_dependencies, path.parent, deps, source)

            module_name = f"_tau_ext_{hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                errors.append(
                    ExtensionError(
                        extension_path=str_path,
                        event="load",
                        error=f"Cannot create module spec for {path}",
                    )
                )
                return None, errors

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            register_fn = getattr(module, _ENTRY_POINT, None)
            if register_fn is None or not callable(register_fn):
                errors.append(
                    ExtensionError(
                        extension_path=str_path,
                        event="load",
                        error=f"No '{_ENTRY_POINT}(tau)' function in {path.name}",
                    )
                )
                return None, errors

            ext = Extension(path=str_path, config=config, source=source)
            api = ExtensionAPI(
                extension=ext,
                llm=self._llm,  # type: ignore[arg-type]
                settings=self._settings,  # type: ignore[arg-type]
                cwd=self._cwd,
                runtime_ref=self._runtime_ref,
            )

            result = register_fn(api)
            if inspect.isawaitable(result):
                await result

            self._attach_manifest_panel(ext, path)

            return ext, errors

        except Exception:
            tb = traceback.format_exc()
            errors.append(
                ExtensionError(
                    extension_path=str_path,
                    event="load",
                    error=tb.strip().splitlines()[-1],
                    stack=tb,
                )
            )
            return None, errors

    def _attach_manifest_panel(self, ext: Extension, path: Path) -> None:
        """Auto-build a /settings sub-panel from the manifest ``settings`` schema.

        Skipped when the extension already registered a panel itself (manual
        ``register_settings`` wins) or when no schema is declared.
        """
        if ext.settings_registrations:
            return
        if self._settings is None:
            return  # no settings manager → nothing to persist changes to
        settings = self._settings
        schema = self._subdir_settings.get(path.parent.resolve())
        if not schema:
            return

        from tau.modes.interactive.components.settings_selector import build_manifest_panel

        ext_dir = path.parent
        try:
            settings_path = str(ext_dir.relative_to(self._cwd))
        except ValueError:
            settings_path = str(ext_dir)
        module_path = str(path)  # absolute path of the loaded entry file

        def _apply(key: str, value: Any) -> None:
            # Persist to settings.json, then reload just this extension so the
            # change takes effect live without re-running other extensions.
            settings.set_extension_config_key(settings_path, key, value)
            runtime = self._runtime_ref.runtime if self._runtime_ref is not None else None
            if runtime is None:
                return
            reload_one = getattr(runtime, "reload_extension", None)

            def _log_reload_error(task: asyncio.Task) -> None:
                if not task.cancelled() and (exc := task.exception()):
                    _log.error("extension reload failed", exc_info=exc)

            if reload_one is not None:
                asyncio.ensure_future(reload_one(module_path)).add_done_callback(_log_reload_error)
            else:  # fall back to full reload if single-extension reload is unavailable
                reload_all = getattr(runtime, "reload_extensions", None)
                if reload_all is not None:
                    asyncio.ensure_future(reload_all()).add_done_callback(_log_reload_error)

        reg = build_manifest_panel(
            schema,
            ext.config,
            default_title=ext_dir.name,
            apply=_apply,
        )
        if reg is not None:
            ext.settings_registrations.append(reg)
