"""Tests for the `tau update` flags added for parity with `pi update`:
--extensions (packages only, not Tau) and --force (reinstall Tau even if latest).
"""

from __future__ import annotations

import subprocess
import types

import pytest
from click.testing import CliRunner

import tau.console.commands.update as update_mod


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_name_with_extensions_errors(self, runner):
        r = runner.invoke(update_mod.update, ["somepkg", "--extensions"])
        assert r.exit_code != 0
        assert "NAME cannot be combined with --extensions" in r.output

    def test_all_with_extensions_errors(self, runner):
        r = runner.invoke(update_mod.update, ["--all", "--extensions"])
        assert r.exit_code != 0
        assert "--all and --extensions cannot be combined" in r.output

    def test_force_with_name_errors(self, runner):
        r = runner.invoke(update_mod.update, ["somepkg", "--force"])
        assert r.exit_code != 0
        assert "--force only applies to updating Tau itself" in r.output

    def test_force_with_extensions_errors(self, runner):
        r = runner.invoke(update_mod.update, ["--extensions", "--force"])
        assert r.exit_code != 0
        assert "--force only applies to updating Tau itself" in r.output


# ── --force is threaded into the self-update ─────────────────────────────────


class TestForceFlag:
    def test_force_passed_to_update_tau(self, runner, monkeypatch):
        seen: list[bool] = []
        monkeypatch.setattr(update_mod, "_update_tau", lambda force=False: seen.append(force))

        runner.invoke(update_mod.update, ["--force"])
        assert seen == [True]

        seen.clear()
        runner.invoke(update_mod.update, [])
        assert seen == [False]

    def test_update_tau_force_adds_reinstall_switch(self, monkeypatch):
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return types.SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        update_mod._update_tau(force=True)
        assert captured, "installer was not invoked"
        cmd = captured[0]
        assert any(flag in cmd for flag in ("--force", "--reinstall", "--force-reinstall"))

        captured.clear()
        update_mod._update_tau(force=False)
        cmd = captured[0]
        assert not any(flag in cmd for flag in ("--force", "--reinstall", "--force-reinstall"))


# ── --extensions updates packages but never Tau ──────────────────────────────


class TestExtensionsFlag:
    def _wire(self, monkeypatch, package_names: list[str]):
        """Stub Tau self-update and package updates; return the record dicts."""
        record = {"tau_called": False, "updated": []}
        monkeypatch.setattr(
            update_mod, "_update_tau", lambda *a, **k: record.__setitem__("tau_called", True)
        )

        from tau.packages.manager import PackageManager
        from tau.settings.manager import SettingsManager

        pkgs = [
            types.SimpleNamespace(name=n, index_url=None, extra_index_urls=None)
            for n in package_names
        ]

        import tau.trust.manager as trust_mod

        fake_settings = types.SimpleNamespace(
            get_all_packages=lambda: pkgs,
            get_packages=lambda local=False: [] if local else pkgs,
            update_package_version=lambda *a, **k: None,
            flush=_noop_async,
        )
        monkeypatch.setattr(trust_mod, "create_project_settings_manager", lambda cwd: fake_settings)
        def _fake_update(self, name, **k):
            record["updated"].append(name)
            return "1.0"

        monkeypatch.setattr(PackageManager, "update", _fake_update)
        monkeypatch.setattr(SettingsManager, "update_package_version", lambda *a, **k: None)
        return record

    def test_extensions_updates_packages_not_tau(self, runner, monkeypatch):
        record = self._wire(monkeypatch, ["pkg-a", "pkg-b"])
        r = runner.invoke(update_mod.update, ["--extensions"])
        assert r.exit_code == 0, r.output
        assert record["tau_called"] is False  # Tau itself must NOT be updated
        assert set(record["updated"]) == {"pkg-a", "pkg-b"}

    def test_all_updates_packages_and_tau(self, runner, monkeypatch):
        record = self._wire(monkeypatch, ["pkg-a"])
        r = runner.invoke(update_mod.update, ["--all"])
        assert r.exit_code == 0, r.output
        assert record["tau_called"] is True
        assert record["updated"] == ["pkg-a"]

    def test_extensions_with_no_packages_reports_and_skips_tau(self, runner, monkeypatch):
        record = self._wire(monkeypatch, [])
        r = runner.invoke(update_mod.update, ["--extensions"])
        assert r.exit_code == 0, r.output
        assert record["tau_called"] is False
        assert "No extension packages to update." in r.output


async def _noop_async() -> None:
    return None
