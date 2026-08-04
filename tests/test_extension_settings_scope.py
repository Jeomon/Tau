"""A manifest-driven /settings write must land in the scope that owns the extension.

``ExtensionLoader._attach_manifest_panel`` builds the auto-generated settings
sub-panel for an extension that declares a ``settings`` schema in its
manifest, and wires the panel's apply callback to
``SettingsManager.set_extension_config_key``. That callback used to be given no
scope at all, so *every* extension's settings were persisted to global
settings — including project extensions, which were additionally keyed by the
project-relative path the loader computed for them. Since the /extensions panel
derives an extension's scope from the list it was found in, that stray global
record surfaced as a second copy of the extension under "Global", carrying a
path meaningless outside that one working directory.

These drive the real panel registration rather than calling
``set_extension_config_key`` directly, so the scope the loader passes is what is
actually under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tau.extensions.api import Extension
from tau.extensions.loader import ExtensionLoader
from tau.settings.manager import SettingsManager

_MANIFEST = {
    "tau": {
        "name": "Browser use",
        "settings": {
            "title": "Browser use",
            "fields": [
                {"key": "cdp_url", "label": "CDP URL", "type": "string", "default": ""},
            ],
        },
    }
}


def _make_extension_dir(root: Path, name: str) -> Path:
    ext_dir = root / name
    ext_dir.mkdir(parents=True)
    (ext_dir / "__init__.py").write_text("def register(tau): pass\n")
    (ext_dir / "manifest.json").write_text(json.dumps(_MANIFEST))
    return ext_dir


def _attach(cwd: Path, ext_dir: Path, source: str) -> tuple[SettingsManager, Extension]:
    """Run the real _attach_manifest_panel for an extension of the given source.

    ``config_dir`` is mandatory here, not cosmetic: these writes go through the
    real file-backed storage, and without it the global scope resolves to the
    developer's own ``~/.tau/settings.json`` and the tests append junk entries
    to it.
    """
    config_dir = cwd / "_global_config"
    config_dir.mkdir(exist_ok=True)
    sm = SettingsManager.create(cwd, config_dir=config_dir, project_trusted=True)
    loader = ExtensionLoader(cwd=cwd, settings=sm)
    entry = ext_dir / "__init__.py"
    # Populates _subdir_settings from manifest.json, exactly as discovery does.
    loader._subdir_entries(ext_dir)
    ext = Extension(path=str(entry), config={}, source=source)
    loader._attach_manifest_panel(ext, entry)
    assert ext.settings_registrations, "manifest schema should have produced a panel"
    return sm, ext


def _apply(sm: SettingsManager, ext: Extension, key: str, value: str) -> None:
    """Trigger the panel's on_change and drain the async write queue.

    ``set_*`` enqueues an asyncio task, so it needs a running loop.
    """

    async def _run() -> None:
        ext.settings_registrations[0].on_change(key, value)
        if sm._write_queue is not None:
            await sm._write_queue

    asyncio.run(_run())


def _paths(settings_obj) -> list[str]:
    ext = settings_obj.extensions
    return [e.path for e in ext.list] if ext and ext.list else []


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestProjectExtension:
    def test_write_goes_to_project_settings(self, project):
        ext_dir = _make_extension_dir(project / ".tau" / "extensions", "browser_use")
        sm, ext = _attach(project, ext_dir, "project")

        _apply(sm, ext, "cdp_url", "9222")

        assert _paths(sm.project_settings) == [".tau/extensions/browser_use"]

    def test_no_global_entry_is_created(self, project):
        ext_dir = _make_extension_dir(project / ".tau" / "extensions", "browser_use")
        sm, ext = _attach(project, ext_dir, "project")

        _apply(sm, ext, "cdp_url", "9222")

        assert _paths(sm.global_settings) == [], (
            "a project extension must not leave a record in global settings — "
            "the /extensions panel would list it a second time under 'Global'"
        )

    def test_the_value_is_actually_persisted(self, project):
        ext_dir = _make_extension_dir(project / ".tau" / "extensions", "browser_use")
        sm, ext = _attach(project, ext_dir, "project")

        _apply(sm, ext, "cdp_url", "9222")

        entry = sm.project_settings.extensions.list[0]
        assert entry.settings == {"cdp_url": "9222"}


class TestGlobalExtension:
    def test_write_goes_to_global_settings_with_an_absolute_path(self, project, tmp_path):
        ext_dir = _make_extension_dir(tmp_path / "global_root" / "extensions", "voice")
        sm, ext = _attach(project, ext_dir, "global")

        _apply(sm, ext, "cdp_url", "9222")

        assert _paths(sm.global_settings) == [str(ext_dir)]
        assert _paths(sm.project_settings) == []

    def test_a_global_extension_under_the_cwd_still_stores_an_absolute_path(self, project):
        """The old code tried ``relative_to(cwd)`` for every non-builtin and only
        avoided a relative global path because that call happened to raise. A
        global extension that *does* sit under the working directory — running
        tau from ``~`` while extensions live in ``~/.tau/extensions`` — would
        otherwise be recorded as a project-relative path in global settings.
        """
        ext_dir = _make_extension_dir(project / ".tau" / "extensions", "voice")
        sm, ext = _attach(project, ext_dir, "global")

        _apply(sm, ext, "cdp_url", "9222")

        stored = _paths(sm.global_settings)
        assert stored == [str(ext_dir)]
        assert Path(stored[0]).is_absolute()


class TestBuiltinExtension:
    def test_builtin_still_writes_globally_with_an_absolute_path(self, project, tmp_path):
        ext_dir = _make_extension_dir(tmp_path / "builtins" / "extensions", "web")
        sm, ext = _attach(project, ext_dir, "builtin")

        _apply(sm, ext, "cdp_url", "9222")

        assert _paths(sm.global_settings) == [str(ext_dir)]
        assert _paths(sm.project_settings) == []
