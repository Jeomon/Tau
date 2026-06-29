from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tau.hooks.runtime import ResourcesDiscoverResult
from tau.hooks.service import Hooks
from tau.resources.loader import ResourceLoader
from tau.settings.types import ExtensionEntry, PackageEntry


class _Settings:
    def __init__(
        self,
        *,
        extensions: list[ExtensionEntry] | None = None,
        global_packages: list[PackageEntry] | None = None,
        project_packages: list[PackageEntry] | None = None,
        extensions_enabled: bool = True,
    ) -> None:
        self.extensions = extensions or []
        self.global_packages = global_packages or []
        self.project_packages = project_packages or []
        self.extensions_enabled = extensions_enabled

    def get_all_extension_entries(self) -> list[ExtensionEntry]:
        return self.extensions

    def get_packages(self, local: bool = False) -> list[PackageEntry]:
        return self.project_packages if local else self.global_packages

    def is_extensions_enabled(self) -> bool:
        return self.extensions_enabled


def _resource_package(tmp_path: Path) -> PackageEntry:
    package = tmp_path / "demo"
    for resource in ("extensions", "skills", "prompts", "themes"):
        (package / resource).mkdir(parents=True)
    (package / "extensions" / "main.py").write_text("def register(tau): pass\n")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "tau": {
                    "extensions": ["extensions/main.py"],
                    "skills": ["skills"],
                    "prompts": ["prompts"],
                    "themes": ["themes"],
                }
            }
        )
    )
    return PackageEntry(source=str(package), name="demo", installed_path=str(package))


def test_discover_combines_explicit_package_and_hook_resources(tmp_path: Path) -> None:
    explicit = ExtensionEntry(
        path=str(tmp_path / "explicit.py"),
        settings={"enabled_feature": True},
    )
    disabled = ExtensionEntry(path=str(tmp_path / "disabled.py"), enabled=False)
    package = _resource_package(tmp_path)
    hook_skills = tmp_path / "hook-skills"
    hook_prompts = tmp_path / "hook-prompts"
    hook_themes = tmp_path / "hook-themes"

    hooks = Hooks()

    async def contribute(_event: object) -> ResourcesDiscoverResult:
        return ResourcesDiscoverResult(
            skill_paths=[str(hook_skills)],
            prompt_paths=[str(hook_prompts)],
            theme_paths=[str(hook_themes)],
        )

    hooks.register("resources_discover", contribute)
    settings = _Settings(extensions=[explicit, disabled], global_packages=[package])

    snapshot = asyncio.run(
        ResourceLoader(cwd=tmp_path, settings=settings, hooks=hooks).discover()  # type: ignore[arg-type]
    )

    extension_paths = {Path(entry.path) for entry in snapshot.extension_entries}
    assert Path(explicit.path) in extension_paths
    assert Path(package.installed_path or "") / "extensions" / "main.py" in extension_paths
    assert snapshot.disabled_extension_stems == frozenset({"disabled"})
    assert snapshot.extension_configs["explicit"] == {"enabled_feature": True}
    assert Path(package.installed_path or "") / "skills" in snapshot.skill_paths
    assert hook_skills.resolve() in snapshot.skill_paths
    assert hook_prompts.resolve() in snapshot.prompt_paths
    assert hook_themes.resolve() in snapshot.theme_paths


def test_extensions_toggle_keeps_non_extension_resources(tmp_path: Path) -> None:
    package = _resource_package(tmp_path)
    settings = _Settings(global_packages=[package], extensions_enabled=False)

    snapshot = asyncio.run(
        ResourceLoader(
            cwd=tmp_path,
            settings=settings,  # type: ignore[arg-type]
            hooks=Hooks(),
        ).discover()
    )

    assert snapshot.project_extension_dir is None
    assert snapshot.global_extension_dir is None
    assert snapshot.extension_entries == ()
    assert snapshot.skill_paths
    assert snapshot.prompt_paths
    assert snapshot.theme_paths


def test_discovery_deduplicates_hook_paths(tmp_path: Path) -> None:
    hooks = Hooks()

    async def contribute(_event: object) -> ResourcesDiscoverResult:
        return ResourcesDiscoverResult(skill_paths=[str(tmp_path), str(tmp_path)])

    hooks.register("resources_discover", contribute)
    snapshot = asyncio.run(
        ResourceLoader(
            cwd=tmp_path,
            settings=_Settings(),  # type: ignore[arg-type]
            hooks=hooks,
        ).discover()
    )

    assert snapshot.skill_paths == (tmp_path.resolve(),)
