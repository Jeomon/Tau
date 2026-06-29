from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau.packages.manager import PackageManager


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "demo"
    for resource in ("extensions", "skills", "prompts", "themes"):
        (package / resource).mkdir(parents=True)
    (package / "extensions" / "main.py").write_text("def register(tau): pass\n")
    (package / "skills" / "review").mkdir()
    (package / "skills" / "review" / "SKILL.md").write_text("# Review\n")
    (package / "prompts" / "fix.md").write_text("Fix this\n")
    (package / "themes" / "demo.yaml").write_text("primary: blue\n")
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
    return package


@pytest.mark.parametrize("resource", ["extensions", "skills", "prompts", "themes"])
def test_find_declared_package_resources(tmp_path: Path, resource: str) -> None:
    package = _package(tmp_path)
    manager = PackageManager(tmp_path / "venv")

    paths = manager.find_resource_paths("demo", resource, str(package))

    assert len(paths) == 1
    assert paths[0].exists()


def test_empty_resource_filter_disables_resource(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manager = PackageManager(tmp_path / "venv")

    assert manager.find_resource_paths("demo", "skills", str(package), []) == []


def test_resource_filter_selects_declared_path(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manager = PackageManager(tmp_path / "venv")

    paths = manager.find_resource_paths("demo", "prompts", str(package), ["prompts"])

    assert paths == [(package / "prompts").resolve()]


def test_unknown_resource_is_rejected(tmp_path: Path) -> None:
    manager = PackageManager(tmp_path / "venv")

    with pytest.raises(ValueError, match="Unsupported package resource"):
        manager.find_resource_paths("demo", "unknown")
