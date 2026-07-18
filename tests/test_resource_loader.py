from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from tau.hooks.runtime import ResourcesDiscoverResult
from tau.hooks.service import Hooks
from tau.resources.loader import DefaultResourceLoader
from tau.resources.types import ContextFile, ResourceContext
from tau.runtime.types import RuntimeConfig
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

    def is_batching(self) -> bool:
        # Matches SettingsManager's real default (see settings/manager.py) —
        # this fake predates DefaultResourceLoader.discover() gaining a
        # dependency on it (it skips pruning stale settings.json extension
        # records while a real batch is in progress; nothing here simulates
        # batch mode, so always report "not batching").
        return False

    def prune_dangling_extensions(self, cwd: Path) -> list:
        # No-op: pruning stale settings.json records has its own coverage
        # elsewhere (settings/manager.py's own tests) — these tests are about
        # resource *discovery*, and self.extensions here is a fixed list the
        # test author constructed directly, not something meant to be mutated
        # out from under it mid-test.
        return []


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
        DefaultResourceLoader().discover(
            ResourceContext(cwd=tmp_path, settings=settings, hooks=hooks)  # type: ignore[arg-type]
        )
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
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=settings,  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
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
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(),  # type: ignore[arg-type]
                hooks=hooks,
            )
        )
    )

    assert snapshot.skill_paths == (tmp_path.resolve(),)


def test_runtime_config_accepts_replaceable_resource_loader(tmp_path: Path) -> None:
    loader = DefaultResourceLoader()

    config = RuntimeConfig(cwd=tmp_path, resource_loader=loader)

    assert config.resource_loader is loader


def test_default_loader_supports_fine_grained_overrides(tmp_path: Path) -> None:
    context_path = tmp_path / "virtual" / "AGENTS.md"
    loader = DefaultResourceLoader(
        skills_override=lambda paths: (*paths, tmp_path / "extra-skills"),
        context_files_override=lambda _files: (
            ContextFile(path=context_path, content="Virtual instructions"),
        ),
        system_prompt_override=lambda: "Custom identity",
    )

    snapshot = asyncio.run(
        loader.discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(),  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
    )

    assert snapshot.skill_paths == (tmp_path / "extra-skills",)
    assert snapshot.context_files == (
        ContextFile(path=context_path, content="Virtual instructions"),
    )
    assert snapshot.system_prompt == "Custom identity"


def test_default_loader_discovers_context_files(tmp_path: Path) -> None:
    context_path = tmp_path / "AGENTS.md"
    context_path.write_text("# Project rules")

    snapshot = asyncio.run(
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(),  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
    )

    assert snapshot.context_files == (ContextFile(path=context_path, content="# Project rules"),)


def test_diagnostics_report_invalid_configured_extensions(tmp_path: Path) -> None:
    wrong_suffix = tmp_path / "extension.txt"
    wrong_suffix.write_text("not python")
    settings = _Settings(
        extensions=[
            ExtensionEntry(path=str(tmp_path / "missing.py")),
            ExtensionEntry(path=str(wrong_suffix)),
        ]
    )

    snapshot = asyncio.run(
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=settings,  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
    )

    assert {(item.severity, item.message) for item in snapshot.diagnostics} == {
        ("error", "Configured extension path does not exist"),
        ("error", "Extension entry file must use the .py suffix"),
    }


def test_diagnostics_report_invalid_package_resources(tmp_path: Path) -> None:
    package = tmp_path / "broken-package"
    package.mkdir()
    (package / "manifest.json").write_text(json.dumps({"tau": {"skills": ["missing-skills"]}}))
    entry = PackageEntry(
        source=str(package),
        name="broken",
        installed_path=str(package),
        skills=["selected-skill"],
    )

    snapshot = asyncio.run(
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(global_packages=[entry]),  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
    )

    messages = {item.message for item in snapshot.diagnostics}
    assert "Package manifest declares a missing skills resource" in messages
    assert "Configured package skills resource 'selected-skill' was not found" in messages


def test_diagnostics_report_missing_package_and_malformed_manifest(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("{not json")
    invalid_root = tmp_path / "invalid-root"
    invalid_root.mkdir()
    (invalid_root / "manifest.json").write_text("[]")
    packages = [
        PackageEntry(
            source="missing",
            name="missing",
            installed_path=str(tmp_path / "missing"),
        ),
        PackageEntry(
            source=str(malformed),
            name="malformed",
            installed_path=str(malformed),
        ),
        PackageEntry(
            source=str(invalid_root),
            name="invalid-root",
            installed_path=str(invalid_root),
        ),
    ]

    snapshot = asyncio.run(
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(global_packages=packages),  # type: ignore[arg-type]
                hooks=Hooks(),
            )
        )
    )

    messages = {item.message for item in snapshot.diagnostics}
    assert "Installed package directory for 'missing' does not exist" in messages
    assert any(message.startswith("Could not parse package manifest:") for message in messages)
    assert "Package manifest root must be an object" in messages


def test_diagnostics_report_missing_hook_paths(tmp_path: Path) -> None:
    hooks = Hooks()
    missing = tmp_path / "missing-skills"

    async def contribute(_event: object) -> ResourcesDiscoverResult:
        return ResourcesDiscoverResult(skill_paths=[str(missing)])

    hooks.register("resources_discover", contribute)
    snapshot = asyncio.run(
        DefaultResourceLoader().discover(
            ResourceContext(
                cwd=tmp_path,
                settings=_Settings(),  # type: ignore[arg-type]
                hooks=hooks,
            )
        )
    )

    assert len(snapshot.diagnostics) == 1
    assert snapshot.diagnostics[0].severity == "warning"
    assert snapshot.diagnostics[0].source == "hook:resources_discover"
    assert snapshot.diagnostics[0].path == missing


def test_diagnostics_report_context_read_errors(tmp_path: Path) -> None:
    context_path = tmp_path / "AGENTS.md"
    context_path.write_text("unreadable")
    original_read_text = Path.read_text

    def fail_context_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.name.lower() == context_path.name.lower():
            raise OSError("permission denied")
        return original_read_text(path, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "read_text", fail_context_read):
        snapshot = asyncio.run(
            DefaultResourceLoader().discover(
                ResourceContext(
                    cwd=tmp_path,
                    settings=_Settings(),  # type: ignore[arg-type]
                    hooks=Hooks(),
                )
            )
        )

    assert snapshot.context_files == ()
    assert snapshot.diagnostics[0].source == "context-file"
    assert "permission denied" in snapshot.diagnostics[0].message
