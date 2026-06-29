from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tau.hooks.runtime import ResourcesDiscoverResult
from tau.hooks.types import ResourcesDiscoverEvent
from tau.packages.manager import PackageManager
from tau.packages.utils import add_site_packages_path
from tau.resources.types import ResourceSnapshot
from tau.settings.paths import (
    get_builtins_dir,
    get_extensions_dir,
    get_packages_venv,
)
from tau.settings.types import ExtensionEntry

if TYPE_CHECKING:
    from tau.extensions.api import _RuntimeRef
    from tau.extensions.loader import ExtensionLoader
    from tau.hooks.service import Hooks
    from tau.inference.api.text.service import TextLLM
    from tau.settings.manager import SettingsManager


class ResourceLoader:
    """Discover every runtime resource through one startup/reload path."""

    def __init__(
        self,
        *,
        cwd: Path,
        settings: SettingsManager,
        hooks: Hooks,
    ) -> None:
        self.cwd = cwd.resolve()
        self.settings = settings
        self.hooks = hooks

    async def discover(self) -> ResourceSnapshot:
        """Return a deduplicated snapshot of local, package, and hook resources."""
        extension_entries: list[ExtensionEntry] = []
        extension_sources: dict[str, str] = {}
        disabled_stems: set[str] = set()
        extension_configs: dict[str, dict] = {}

        for entry in self.settings.get_all_extension_entries():
            stem = Path(entry.path).stem
            if entry.enabled:
                extension_entries.append(entry)
                extension_configs[stem] = entry.settings or {}
            else:
                disabled_stems.add(stem)

        skill_paths: list[Path] = []
        prompt_paths: list[Path] = []
        theme_paths: list[Path] = []

        packages = [
            *((package, False) for package in self.settings.get_packages(local=False)),
            *((package, True) for package in self.settings.get_packages(local=True)),
        ]
        for scope_local in (False, True):
            if any(local == scope_local for _package, local in packages):
                manager = PackageManager(get_packages_venv(self.cwd if scope_local else None))
                add_site_packages_path(manager.site_packages())

        for package, local in packages:
            if not package.enabled:
                continue
            manager = PackageManager(get_packages_venv(self.cwd if local else None))
            extension_files = manager.find_resource_paths(
                package.name,
                "extensions",
                package.installed_path,
                package.extensions,
            )
            if not extension_files and package.extensions is None:
                extension_files = manager.find_extension_files(package.name, package.installed_path)
            for path in extension_files:
                extension_entries.append(ExtensionEntry(path=str(path), name=package.name))
                extension_sources[str(path.resolve())] = "package"

            skill_paths.extend(
                manager.find_resource_paths(
                    package.name, "skills", package.installed_path, package.skills
                )
            )
            prompt_paths.extend(
                manager.find_resource_paths(
                    package.name, "prompts", package.installed_path, package.prompts
                )
            )
            theme_paths.extend(
                manager.find_resource_paths(
                    package.name, "themes", package.installed_path, package.themes
                )
            )

        results = await self.hooks.emit(ResourcesDiscoverEvent(cwd=str(self.cwd)))
        for result in results:
            if not isinstance(result, ResourcesDiscoverResult):
                continue
            skill_paths.extend(Path(path).expanduser().resolve() for path in result.skill_paths)
            prompt_paths.extend(Path(path).expanduser().resolve() for path in result.prompt_paths)
            theme_paths.extend(Path(path).expanduser().resolve() for path in result.theme_paths)

        extensions_enabled = self.settings.is_extensions_enabled()
        return ResourceSnapshot(
            builtins_extension_dir=get_builtins_dir() / "extensions",
            project_extension_dir=get_extensions_dir(self.cwd) if extensions_enabled else None,
            global_extension_dir=get_extensions_dir() if extensions_enabled else None,
            extension_entries=tuple(extension_entries) if extensions_enabled else (),
            extension_sources=extension_sources if extensions_enabled else {},
            disabled_extension_stems=frozenset(disabled_stems)
            if extensions_enabled
            else frozenset(),
            extension_configs=extension_configs if extensions_enabled else {},
            skill_paths=tuple(dict.fromkeys(skill_paths)),
            prompt_paths=tuple(dict.fromkeys(prompt_paths)),
            theme_paths=tuple(dict.fromkeys(theme_paths)),
        )

    def create_extension_loader(
        self,
        snapshot: ResourceSnapshot,
        *,
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
            settings=self.settings,
            cwd=self.cwd,
            runtime_ref=runtime_ref,
        )

    def apply_registries(self, snapshot: ResourceSnapshot) -> None:
        """Reload skills, prompts, and themes from the same snapshot."""
        from tau.prompts.registry import prompt_registry
        from tau.skills.registry import skill_registry
        from tau.themes.registry import theme_registry

        skill_registry.reload(
            cwd=self.cwd,
            extra_paths=[str(path) for path in snapshot.skill_paths],
        )
        prompt_registry.reload(
            cwd=self.cwd,
            extra_paths=[str(path) for path in snapshot.prompt_paths],
        )
        theme_registry.reload_external(
            cwd=self.cwd,
            extra_paths=list(snapshot.theme_paths),
        )
