from __future__ import annotations

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
