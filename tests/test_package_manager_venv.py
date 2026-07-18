from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tau.packages.manager import PackageManager


def test_ensure_venv_pins_uv_to_running_interpreter(tmp_path: Path, monkeypatch) -> None:
    """uv venv without --python picks its own default toolchain, which can differ
    from the interpreter actually running Tau (see tau/console/commands/doctor.py's
    venv-mismatch check). ensure_venv() must pin explicitly.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("tau.packages.manager.shutil.which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr("tau.packages.manager.subprocess.run", fake_run)

    manager = PackageManager(tmp_path / "venv")
    manager.ensure_venv()

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:2] == ["uv", "venv"]
    assert "--python" in cmd
    assert cmd[cmd.index("--python") + 1] == sys.executable


def test_ensure_venv_falls_back_to_stdlib_venv_without_uv(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("tau.packages.manager.shutil.which", lambda name: None)
    monkeypatch.setattr("tau.packages.manager.subprocess.run", fake_run)

    manager = PackageManager(tmp_path / "venv")
    manager.ensure_venv()

    assert len(calls) == 1
    assert calls[0] == [sys.executable, "-m", "venv", str(tmp_path / "venv")]


def test_ensure_venv_no_op_when_python_already_exists(tmp_path: Path, monkeypatch) -> None:
    venv_dir = tmp_path / "venv"
    python_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python_dir.mkdir(parents=True)
    (python_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("")

    calls: list[list[str]] = []
    monkeypatch.setattr("tau.packages.manager.subprocess.run", lambda cmd, **kw: calls.append(cmd))

    manager = PackageManager(venv_dir)
    manager.ensure_venv()

    assert calls == []


def test_install_requirements_passes_a_timeout(tmp_path: Path, monkeypatch) -> None:
    """A hung pip/uv (a private index prompting for credentials headlessly,
    a stalled network) previously blocked forever with no recovery.
    install_requirements() runs on the extension-loading path — awaited
    inside an asyncio.gather() over every discovered extension — so one hung
    install there stalls the whole gather(), not just that one extension.
    """
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("tau.packages.manager.shutil.which", lambda name: None)
    monkeypatch.setattr("tau.packages.manager.subprocess.run", fake_run)

    venv_dir = tmp_path / "venv"
    python_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python_dir.mkdir(parents=True)
    (python_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("")

    manager = PackageManager(venv_dir)
    manager.install_requirements(["some-package>=1.0"])

    assert len(calls) == 1
    assert calls[0].get("timeout") is not None
    assert calls[0]["timeout"] > 0


def test_site_packages_returns_none_on_timeout_rather_than_raising(
    tmp_path: Path, monkeypatch
) -> None:
    """site_packages() has always tolerated failure by returning None rather
    than raising — callers (e.g. resource discovery) aren't wrapped in a
    broad try/except around this call. A timeout must fail the same way,
    not surface as a new, previously-impossible crash.
    """
    venv_dir = tmp_path / "venv"
    python_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python_dir.mkdir(parents=True)
    (python_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr("tau.packages.manager.subprocess.run", fake_run)

    manager = PackageManager(venv_dir)
    assert manager.site_packages() is None


def test_get_installed_version_returns_none_on_timeout_rather_than_raising(
    tmp_path: Path, monkeypatch
) -> None:
    venv_dir = tmp_path / "venv"
    python_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python_dir.mkdir(parents=True)
    (python_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr("tau.packages.manager.subprocess.run", fake_run)

    manager = PackageManager(venv_dir)
    assert manager._get_installed_version("some-package") is None
