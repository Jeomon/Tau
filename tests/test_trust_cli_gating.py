"""Regression tests: `tau` subcommands must not read untrusted project settings.

Before this gate, every subcommand built its settings manager with
``SettingsManager.create(cwd)``, whose ``project_trusted`` defaulted to True. That
merged `.tau/settings.json` from any directory, trusted or not. The severe case was
`tau update --all`: it read project package entries and passed their
attacker-controlled ``index_url`` straight to ``pip install`` (see
tau/packages/manager.py:update), giving a hostile repository code execution when a
user ran the command inside it.

The defaults now fail closed, and callers resolve trust explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau.settings.manager import SettingsManager
from tau.trust.manager import (
    create_project_settings_manager,
    resolve_project_trust,
    trust_store,
)


@pytest.fixture(autouse=True)
def _isolated_trust_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the trust store at a scratch file so tests never read the real one."""
    monkeypatch.setattr(trust_store, "_path", tmp_path / "trust.json")


def _project(tmp_path: Path, *, index_url: str = "https://evil.example/simple") -> Path:
    """A project carrying a trust input and a hostile project-scoped package entry."""
    cwd = tmp_path / "repo"
    (cwd / ".tau").mkdir(parents=True)
    (cwd / ".tau" / "settings.json").write_text(
        json.dumps(
            {
                "packages": {
                    "list": [
                        {
                            "name": "evil-pkg",
                            "source": "pypi:evil-pkg",
                            "enabled": True,
                            "index_url": index_url,
                        }
                    ]
                }
            }
        )
    )
    return cwd


# ── Defaults fail closed ──────────────────────────────────────────────────────


def test_settings_manager_defaults_to_untrusted(tmp_path: Path) -> None:
    cwd = _project(tmp_path)

    sm = SettingsManager.create(cwd=cwd, config_dir=tmp_path / "cfg")

    assert sm.is_project_trusted() is False
    assert sm.get_packages(local=True) == []
    assert sm.get_all_packages() == []


# ── Trust resolution ──────────────────────────────────────────────────────────


def test_directory_without_trust_inputs_needs_no_decision(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert resolve_project_trust(plain) is True


def test_untrusted_project_withholds_package_entries(tmp_path: Path) -> None:
    cwd = _project(tmp_path)

    sm = create_project_settings_manager(cwd, config_dir=tmp_path / "cfg")

    assert sm.is_project_trusted() is False
    # This is the payload that reached `pip install --index-url`.
    assert sm.get_all_packages() == []


def test_stored_trust_decision_restores_package_entries(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    trust_store.set(str(cwd), True)

    sm = create_project_settings_manager(cwd, config_dir=tmp_path / "cfg")

    assert sm.is_project_trusted() is True
    packages = sm.get_all_packages()
    assert [p.name for p in packages] == ["evil-pkg"]


def test_explicit_override_wins_over_stored_decision(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    trust_store.set(str(cwd), True)

    assert resolve_project_trust(cwd, override=False) is False


def test_stored_denial_keeps_project_settings_withheld(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    trust_store.set(str(cwd), False)

    sm = create_project_settings_manager(cwd, config_dir=tmp_path / "cfg")

    assert sm.is_project_trusted() is False
    assert sm.get_all_packages() == []


# ── `tau update` must not reach pip with untrusted package entries ────────────


def _run_update_all(cwd: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Invoke `tau update --all` in *cwd*, returning the package names it tried to update.

    PackageManager.update is the call that shells out to `pip install --index-url
    <project-controlled>`; recording it proves whether an untrusted entry reaches it.
    """
    from click.testing import CliRunner

    import tau.console.commands.update as update_mod
    from tau.packages.manager import PackageManager

    attempted: list[str] = []

    def _record(self, name: str, **kwargs) -> str | None:
        attempted.append(name)
        return "1.0.0"

    monkeypatch.setattr(PackageManager, "update", _record)
    # The version write-back enqueues an async settings write; CliRunner has no
    # event loop, so stub it out. Only the pip-facing call matters here.
    monkeypatch.setattr(SettingsManager, "update_package_version", lambda *a, **k: None)
    # `tau update --all` also upgrades tau itself; that is out of scope here.
    monkeypatch.setattr(update_mod, "_update_tau", lambda: None)
    monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: cwd))
    # Redirect the *global* config dir into tmp so the developer's own
    # ~/.tau/settings.json (and any project_trust policy in it) cannot decide the
    # outcome. Project scope must keep its real <cwd>/.tau layout, or the project
    # settings file is never found and these tests pass vacuously.
    from tau.settings.paths import CONFIG_DIR_NAME

    monkeypatch.setattr(
        "tau.settings.paths.get_config_dir",
        lambda cwd=None: config_dir if cwd is None else cwd / CONFIG_DIR_NAME,
    )

    result = CliRunner().invoke(update_mod.update, ["--all"])
    assert result.exit_code == 0, result.output
    return attempted


def test_update_all_skips_untrusted_project_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _project(tmp_path)

    attempted = _run_update_all(cwd, tmp_path / "cfg", monkeypatch)

    assert attempted == []


def test_update_all_includes_trusted_project_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _project(tmp_path)
    trust_store.set(str(cwd), True)

    attempted = _run_update_all(cwd, tmp_path / "cfg", monkeypatch)

    assert attempted == ["evil-pkg"]
