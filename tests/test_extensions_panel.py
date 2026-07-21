"""The /extensions panel lists what is loaded, not only what is configured.

Extensions are found by scanning the extension directories, so one can be
loaded and working while absent from settings.json's ``extensions.list``.
Before this, the panel showed only configured entries — a freshly added
extension was invisible, and could not be disabled without editing the file
by hand.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.interactive.commands.extensions import open_config_panel
from tau.settings.manager import SettingsManager
from tau.settings.types import ExtensionEntry


class _Layout:
    def __init__(self) -> None:
        self.entries: list = []
        self.on_toggle = None
        self.on_close = None

    def open_config_selector(self, entries, on_toggle, on_close) -> None:
        self.entries, self.on_toggle, self.on_close = entries, on_toggle, on_close


def _ctx(tmp_path: Path, *, configured: list[ExtensionEntry], loaded: list[tuple[str, str]]):
    """A CommandContext stand-in. ``loaded`` is [(path, source), ...]."""
    sm = SettingsManager.in_memory()
    sm.project_settings.extensions = SimpleNamespace(enabled=True, list=list(configured))
    sm.global_settings.extensions = SimpleNamespace(enabled=True, list=[])

    saved: dict[str, list] = {}
    sm.set_project_extension_list = lambda entries: saved.__setitem__("project", entries)
    sm.set_extension_list = lambda entries: saved.__setitem__("global", entries)

    extension_runtime = SimpleNamespace(
        get_extensions=lambda: [SimpleNamespace(path=p, source=s) for p, s in loaded]
    )
    layout = _Layout()
    notes: list[str] = []
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(
            settings_manager=sm,
            extension_runtime=extension_runtime,
            reload_extensions=lambda: None,
        ),
        layout=layout,
        notify=notes.append,
    )
    return ctx, layout, saved, notes


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ".tau" / "extensions"
    for name in ("configured_ext", "discovered_ext"):
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text("def register(tau): pass\n")
    return root


class TestListing:
    def test_a_discovered_extension_appears_alongside_configured_ones(self, project):
        ctx, layout, _, _ = _ctx(
            project.parent.parent,
            configured=[ExtensionEntry(path=".tau/extensions/configured_ext", enabled=True)],
            loaded=[
                (str(project / "configured_ext" / "__init__.py"), "project"),
                (str(project / "discovered_ext" / "__init__.py"), "project"),
            ],
        )

        open_config_panel(ctx)

        names = {e.name for e in layout.entries}
        assert "configured_ext" in names
        assert "discovered_ext" in names

    def test_a_discovered_extension_is_shown_enabled(self, project):
        ctx, layout, _, _ = _ctx(
            project.parent.parent,
            configured=[],
            loaded=[(str(project / "discovered_ext" / "__init__.py"), "project")],
        )

        open_config_panel(ctx)

        entry = next(e for e in layout.entries if e.name == "discovered_ext")
        assert entry.enabled is True
        assert entry.scope == "project"

    def test_it_is_not_listed_twice_when_also_configured(self, project):
        ctx, layout, _, _ = _ctx(
            project.parent.parent,
            configured=[ExtensionEntry(path=".tau/extensions/configured_ext", enabled=False)],
            loaded=[(str(project / "configured_ext" / "__init__.py"), "project")],
        )

        open_config_panel(ctx)

        matching = [e for e in layout.entries if "configured_ext" in e.path]
        assert len(matching) == 1
        # The configured state wins — it is the persisted truth.
        assert matching[0].enabled is False

    def test_project_paths_are_stored_relative_to_the_project(self, project):
        ctx, layout, _, _ = _ctx(
            project.parent.parent,
            configured=[],
            loaded=[(str(project / "discovered_ext" / "__init__.py"), "project")],
        )

        open_config_panel(ctx)

        entry = next(e for e in layout.entries if e.name == "discovered_ext")
        assert entry.path == ".tau/extensions/discovered_ext"

    def test_unownable_sources_are_skipped(self, project):
        # A package/explicit extension is not owned by either settings list, so
        # toggling it here could not persist — better absent than a dead switch.
        ctx, layout, _, _ = _ctx(
            project.parent.parent,
            configured=[],
            loaded=[(str(project / "discovered_ext" / "__init__.py"), "package")],
        )

        open_config_panel(ctx)

        assert not any(e.name == "discovered_ext" for e in layout.entries)


class TestToggling:
    def test_disabling_a_discovered_extension_creates_its_entry(self, project):
        ctx, layout, saved, _ = _ctx(
            project.parent.parent,
            configured=[],
            loaded=[(str(project / "discovered_ext" / "__init__.py"), "project")],
        )
        open_config_panel(ctx)
        entry = next(e for e in layout.entries if e.name == "discovered_ext")

        layout.on_toggle(entry, False)

        written = saved["project"]
        assert [(e.path, e.enabled) for e in written] == [
            (".tau/extensions/discovered_ext", False)
        ]

    def test_toggling_a_configured_extension_updates_it_in_place(self, project):
        ctx, layout, saved, _ = _ctx(
            project.parent.parent,
            configured=[ExtensionEntry(path=".tau/extensions/configured_ext", enabled=True)],
            loaded=[(str(project / "configured_ext" / "__init__.py"), "project")],
        )
        open_config_panel(ctx)
        entry = next(e for e in layout.entries if "configured_ext" in e.path)

        layout.on_toggle(entry, False)

        written = saved["project"]
        assert len(written) == 1  # updated, not duplicated
        assert written[0].enabled is False
