from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from tau.hooks.runtime import ResourcesDiscoverResult
from tau.hooks.types import ResourcesDiscoverEvent
from tau.packages.manager import PackageManager
from tau.packages.utils import add_site_packages_path
from tau.resources.types import (
    ContextFile,
    ResourceContext,
    ResourceDiagnostic,
    ResourceSnapshot,
)
from tau.settings.paths import (
    get_builtins_dir,
    get_extensions_dir,
    get_packages_venv,
)
from tau.settings.types import ExtensionEntry

if TYPE_CHECKING:
    from tau.extensions.api import _RuntimeRef
    from tau.extensions.loader import ExtensionLoader
    from tau.inference.api.text.service import TextLLM


@runtime_checkable
class ResourceLoader(Protocol):
    """Replaceable interface for runtime resource discovery and loading."""

    async def discover(self, context: ResourceContext) -> ResourceSnapshot:
        """Return the resources available to the runtime."""
        ...

    def create_extension_loader(
        self,
        snapshot: ResourceSnapshot,
        *,
        context: ResourceContext,
        llm: TextLLM,
        runtime_ref: _RuntimeRef,
    ) -> ExtensionLoader:
        """Construct the extension importer for a resource snapshot."""
        ...

    def apply_registries(
        self,
        snapshot: ResourceSnapshot,
        *,
        context: ResourceContext,
    ) -> None:
        """Apply skills, prompts, and themes from a resource snapshot."""
        ...


class DefaultResourceLoader:
    """Default discovery for local, package, and hook-provided resources."""

    def __init__(
        self,
        *,
        extensions_override: Callable[[tuple[ExtensionEntry, ...]], tuple[ExtensionEntry, ...]]
        | None = None,
        skills_override: Callable[[tuple[Path, ...]], tuple[Path, ...]] | None = None,
        prompts_override: Callable[[tuple[Path, ...]], tuple[Path, ...]] | None = None,
        themes_override: Callable[[tuple[Path, ...]], tuple[Path, ...]] | None = None,
        context_files_override: Callable[[tuple[ContextFile, ...]], tuple[ContextFile, ...]]
        | None = None,
        system_prompt_override: Callable[[], str | None] | None = None,
    ) -> None:
        self.extensions_override = extensions_override
        self.skills_override = skills_override
        self.prompts_override = prompts_override
        self.themes_override = themes_override
        self.context_files_override = context_files_override
        self.system_prompt_override = system_prompt_override

    async def discover(self, context: ResourceContext) -> ResourceSnapshot:
        """Return a deduplicated snapshot of local, package, and hook resources."""
        cwd = context.cwd.resolve()
        settings = context.settings
        extension_entries: list[ExtensionEntry] = []
        extension_sources: dict[str, str] = {}
        disabled_stems: set[str] = set()
        extension_configs: dict[str, dict] = {}
        diagnostics: list[ResourceDiagnostic] = []

        for entry in settings.get_all_extension_entries():
            path = Path(entry.path).expanduser().resolve()
            stem = path.stem
            if entry.enabled:
                extension_entries.append(entry)
                extension_configs[stem] = entry.settings or {}
                self._diagnose_resource_path(
                    diagnostics,
                    path,
                    resource="extension",
                    source="settings",
                    severity="error",
                )
            else:
                disabled_stems.add(stem)

        skill_paths: list[Path] = []
        prompt_paths: list[Path] = []
        theme_paths: list[Path] = []

        packages = [
            *((package, False) for package in settings.get_packages(local=False)),
            *((package, True) for package in settings.get_packages(local=True)),
        ]
        for scope_local in (False, True):
            if any(local == scope_local for _package, local in packages):
                manager = PackageManager(get_packages_venv(cwd if scope_local else None))
                add_site_packages_path(manager.site_packages())

        for package, local in packages:
            if not package.enabled:
                continue
            manager = PackageManager(get_packages_venv(cwd if local else None))
            package_source = f"package:{package.name}"
            package_dir = Path(package.installed_path).resolve() if package.installed_path else None
            if package_dir is not None and not package_dir.is_dir():
                diagnostics.append(
                    ResourceDiagnostic(
                        severity="error",
                        message=f"Installed package directory for '{package.name}' does not exist",
                        source=package_source,
                        path=package_dir,
                    )
                )
                continue
            if package_dir is not None:
                manifest_valid = self._diagnose_package_manifest(
                    diagnostics, package.name, package_dir
                )
                if not manifest_valid:
                    continue

            extension_files = manager.find_resource_paths(
                package.name,
                "extensions",
                package.installed_path,
                package.extensions,
            )
            if not extension_files and package.extensions is None:
                extension_files = manager.find_extension_files(package.name, package.installed_path)
            self._diagnose_package_selection(
                diagnostics,
                package.name,
                "extensions",
                package.extensions,
                extension_files,
                package_dir,
            )
            for path in extension_files:
                extension_entries.append(ExtensionEntry(path=str(path), name=package.name))
                extension_sources[str(path.resolve())] = "package"

            package_skills = manager.find_resource_paths(
                package.name, "skills", package.installed_path, package.skills
            )
            package_prompts = manager.find_resource_paths(
                package.name, "prompts", package.installed_path, package.prompts
            )
            package_themes = manager.find_resource_paths(
                package.name, "themes", package.installed_path, package.themes
            )
            for resource, selected, found in (
                ("skills", package.skills, package_skills),
                ("prompts", package.prompts, package_prompts),
                ("themes", package.themes, package_themes),
            ):
                self._diagnose_package_selection(
                    diagnostics,
                    package.name,
                    resource,
                    selected,
                    found,
                    package_dir,
                )
            skill_paths.extend(package_skills)
            prompt_paths.extend(package_prompts)
            theme_paths.extend(package_themes)

        results = await context.hooks.emit(ResourcesDiscoverEvent(cwd=str(cwd)))
        for result in results:
            if not isinstance(result, ResourcesDiscoverResult):
                continue
            for resource, values, destination in (
                ("skill", result.skill_paths, skill_paths),
                ("prompt", result.prompt_paths, prompt_paths),
                ("theme", result.theme_paths, theme_paths),
            ):
                for value in values:
                    path = Path(value).expanduser().resolve()
                    destination.append(path)
                    self._diagnose_resource_path(
                        diagnostics,
                        path,
                        resource=resource,
                        source="hook:resources_discover",
                    )

        extensions_enabled = settings.is_extensions_enabled()
        context_files: tuple[ContextFile, ...] = ()
        if context.load_context_files:
            from tau.agent.prompt.builder import load_project_context_files

            def context_error(path: Path, exc: OSError) -> None:
                diagnostics.append(
                    ResourceDiagnostic(
                        severity="warning",
                        message=f"Could not read context file: {exc}",
                        source="context-file",
                        path=path,
                    )
                )

            context_files = tuple(
                ContextFile(path=path, content=content)
                for content, path in load_project_context_files(cwd, on_error=context_error)
            )

        discovered_extensions = tuple(extension_entries) if extensions_enabled else ()
        discovered_skills = tuple(dict.fromkeys(skill_paths))
        discovered_prompts = tuple(dict.fromkeys(prompt_paths))
        discovered_themes = tuple(dict.fromkeys(theme_paths))
        original_extensions = discovered_extensions
        original_skills = discovered_skills
        original_prompts = discovered_prompts
        original_themes = discovered_themes
        if self.extensions_override is not None:
            discovered_extensions = self.extensions_override(discovered_extensions)
        if self.skills_override is not None:
            discovered_skills = self.skills_override(discovered_skills)
        if self.prompts_override is not None:
            discovered_prompts = self.prompts_override(discovered_prompts)
        if self.themes_override is not None:
            discovered_themes = self.themes_override(discovered_themes)
        if self.context_files_override is not None:
            context_files = self.context_files_override(context_files)

        for entry in discovered_extensions:
            if entry not in original_extensions:
                self._diagnose_resource_path(
                    diagnostics,
                    Path(entry.path).expanduser().resolve(),
                    resource="extension",
                    source="resource-override",
                    severity="error",
                )
        for resource, paths, originals in (
            ("skill", discovered_skills, original_skills),
            ("prompt", discovered_prompts, original_prompts),
            ("theme", discovered_themes, original_themes),
        ):
            for path in paths:
                if path in originals:
                    continue
                self._diagnose_resource_path(
                    diagnostics,
                    path,
                    resource=resource,
                    source="resource-override",
                )

        return ResourceSnapshot(
            builtins_extension_dir=get_builtins_dir() / "extensions",
            project_extension_dir=get_extensions_dir(cwd) if extensions_enabled else None,
            global_extension_dir=get_extensions_dir() if extensions_enabled else None,
            extension_entries=discovered_extensions,
            extension_sources=extension_sources if extensions_enabled else {},
            disabled_extension_stems=frozenset(disabled_stems)
            if extensions_enabled
            else frozenset(),
            extension_configs=extension_configs if extensions_enabled else {},
            skill_paths=discovered_skills,
            prompt_paths=discovered_prompts,
            theme_paths=discovered_themes,
            context_files=context_files,
            system_prompt=self.system_prompt_override()
            if self.system_prompt_override is not None
            else None,
            diagnostics=self._deduplicate_diagnostics(diagnostics),
        )

    @staticmethod
    def _diagnose_resource_path(
        diagnostics: list[ResourceDiagnostic],
        path: Path,
        *,
        resource: str,
        source: str,
        severity: Literal["warning", "error"] = "warning",
    ) -> None:
        """Record missing or invalid discovered resource paths."""
        if not path.exists():
            diagnostics.append(
                ResourceDiagnostic(
                    severity=severity,
                    message=f"Configured {resource} path does not exist",
                    source=source,
                    path=path,
                )
            )
        elif resource == "extension" and path.is_file() and path.suffix != ".py":
            diagnostics.append(
                ResourceDiagnostic(
                    severity="error",
                    message="Extension entry file must use the .py suffix",
                    source=source,
                    path=path,
                )
            )

    @staticmethod
    def _diagnose_package_manifest(
        diagnostics: list[ResourceDiagnostic],
        package_name: str,
        package_dir: Path,
    ) -> bool:
        """Record malformed manifests and paths declared by them."""
        from tau.settings.paths import get_app_name

        manifest = package_dir / "manifest.json"
        if not manifest.is_file():
            return True
        source = f"package:{package_name}"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    severity="error",
                    message=f"Could not parse package manifest: {exc}",
                    source=source,
                    path=manifest,
                )
            )
            return False
        if not isinstance(data, dict):
            diagnostics.append(
                ResourceDiagnostic(
                    severity="error",
                    message="Package manifest root must be an object",
                    source=source,
                    path=manifest,
                )
            )
            return False
        app_data = data.get(get_app_name().lower(), {})
        if not isinstance(app_data, dict):
            diagnostics.append(
                ResourceDiagnostic(
                    severity="error",
                    message="Package manifest resource section must be an object",
                    source=source,
                    path=manifest,
                )
            )
            return False
        valid = True
        for resource in ("extensions", "skills", "prompts", "themes"):
            values = app_data.get(resource, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                diagnostics.append(
                    ResourceDiagnostic(
                        severity="error",
                        message=f"Package manifest '{resource}' must be a list of paths",
                        source=source,
                        path=manifest,
                    )
                )
                valid = False
                continue
            for value in values:
                path = (package_dir / value).resolve()
                if not path.exists():
                    diagnostics.append(
                        ResourceDiagnostic(
                            severity="error",
                            message=f"Package manifest declares a missing {resource} resource",
                            source=source,
                            path=path,
                        )
                    )
        return valid

    @staticmethod
    def _diagnose_package_selection(
        diagnostics: list[ResourceDiagnostic],
        package_name: str,
        resource: str,
        selected: list[str] | None,
        found: list[Path],
        package_dir: Path | None,
    ) -> None:
        """Record configured package selectors that matched no resource."""
        if not selected:
            return
        matched = {path.name for path in found}
        if package_dir is not None:
            matched.update(
                str(path.relative_to(package_dir))
                for path in found
                if path.is_relative_to(package_dir)
            )
        for value in selected:
            normalized = value.removeprefix("./")
            if value in matched or normalized in matched or Path(normalized).name in matched:
                continue
            diagnostics.append(
                ResourceDiagnostic(
                    severity="warning",
                    message=f"Configured package {resource} resource '{value}' was not found",
                    source=f"package:{package_name}",
                    path=(package_dir / normalized).resolve() if package_dir is not None else None,
                )
            )

    @staticmethod
    def _deduplicate_diagnostics(
        diagnostics: list[ResourceDiagnostic],
    ) -> tuple[ResourceDiagnostic, ...]:
        """Preserve diagnostic order while removing duplicates."""
        return tuple(dict.fromkeys(diagnostics))

    def create_extension_loader(
        self,
        snapshot: ResourceSnapshot,
        *,
        context: ResourceContext,
        llm: TextLLM,
        runtime_ref: _RuntimeRef,
    ) -> ExtensionLoader:
        """Construct the extension importer from a discovered snapshot."""
        from tau.extensions.loader import ExtensionLoader

        return ExtensionLoader(
            builtins_dir=snapshot.builtins_extension_dir,
            project_dir=snapshot.project_extension_dir,
            global_dir=snapshot.global_extension_dir,
            extra_entries=list(snapshot.extension_entries),
            extra_sources=snapshot.extension_sources,
            disabled_stems=set(snapshot.disabled_extension_stems),
            entry_configs=snapshot.extension_configs,
            llm=llm,
            settings=context.settings,
            cwd=context.cwd,
            runtime_ref=runtime_ref,
        )

    def apply_registries(
        self,
        snapshot: ResourceSnapshot,
        *,
        context: ResourceContext,
    ) -> None:
        """Reload skills, prompts, and themes from the same snapshot."""
        from tau.prompts.registry import prompt_registry
        from tau.skills.registry import skill_registry
        from tau.themes.registry import theme_registry

        skill_registry.reload(
            cwd=context.cwd,
            extra_paths=[str(path) for path in snapshot.skill_paths],
        )
        prompt_registry.reload(
            cwd=context.cwd,
            extra_paths=[str(path) for path in snapshot.prompt_paths],
        )
        theme_registry.reload_external(
            cwd=context.cwd,
            extra_paths=list(snapshot.theme_paths),
        )
