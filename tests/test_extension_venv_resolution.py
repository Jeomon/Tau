"""A packages venv built for another Python must not be used for extension deps.

``~/.tau/venv`` is shared across Tau installs and outlives any one of them, so
switching the interpreter Tau runs under strands it on the old version. Its
site-packages is appended to ``sys.path``, so a mismatch makes every native
dependency unimportable — surfacing as the extension's own "missing dependency"
message rather than anything pointing at the real cause.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tau.extensions.loader import resolve_extension_venv_dir


def _make_venv(path: Path, version: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyvenv.cfg").write_text(f"version = {version}.0\n")
    return path


@pytest.fixture
def _packages_venv(tmp_path, monkeypatch):
    venv = tmp_path / "venv"

    def fake_get_packages_venv(_cwd=None):
        return venv

    monkeypatch.setattr("tau.settings.paths.get_packages_venv", fake_get_packages_venv)
    return venv


def test_matching_packages_venv_is_used(_packages_venv, tmp_path):
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    _make_venv(_packages_venv, current)

    assert resolve_extension_venv_dir(tmp_path, "global") == _packages_venv


def test_mismatched_packages_venv_falls_back_to_running_interpreter(_packages_venv, tmp_path):
    _make_venv(_packages_venv, "2.7")  # never the running interpreter

    resolved = resolve_extension_venv_dir(tmp_path, "global")

    assert resolved == Path(sys.prefix)
    assert resolved != _packages_venv


def test_absent_packages_venv_is_still_returned(_packages_venv, tmp_path):
    """Nothing to mismatch yet — the venv is created on first dependency install."""
    assert resolve_extension_venv_dir(tmp_path, "global") == _packages_venv
