"""`tau doctor` should not say "no issues found" when `/reload` says "1 error".

Doctor deliberately never executes extension code — a diagnostic command
should not install dependencies or run arbitrary imports as a side effect. The
cost is that an extension which raises on import is invisible to it: the
manifest parses, every declared file exists, and nothing static separates it
from a healthy one. Only the loader ever knew, and it kept that to itself.

So the loader records the outcome of each load, and doctor reports it. Same
shape as the dependency cache it already reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tau.console.commands.doctor import _load_failure
from tau.extensions.api import ExtensionError
from tau.extensions.loader import _write_load_status, read_load_status


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point the status file at a temporary config directory."""
    target = tmp_path / "config"
    monkeypatch.setattr("tau.settings.paths.get_config_dir", lambda cwd=None: target)
    return target


def _extension(tmp_path: Path, name: str = "broken") -> Path:
    directory = tmp_path / ".tau" / "extensions" / name
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("x = 1\n")
    return directory


def _failure(directory: Path, message: str) -> ExtensionError:
    return ExtensionError(
        extension_path=str(directory / "__init__.py"), event="load", error=message
    )


class TestRecording:
    def test_a_failure_is_recorded_against_its_directory(self, tmp_path, config_dir):
        """Keyed by directory because that is what an inspector scanning the
        filesystem has; the loader's own key is the file inside it."""
        directory = _extension(tmp_path)

        _write_load_status([directory / "__init__.py"], [_failure(directory, "boom")])

        assert read_load_status()[str(directory.resolve())] == {"ok": False, "error": "boom"}

    def test_a_successful_load_is_recorded_too(self, tmp_path, config_dir):
        """Only recording failures would leave a stale one looking current."""
        directory = _extension(tmp_path)

        _write_load_status([directory / "__init__.py"], [])

        assert read_load_status()[str(directory.resolve())] == {"ok": True}

    def test_a_later_success_clears_an_earlier_failure(self, tmp_path, config_dir):
        directory = _extension(tmp_path)
        _write_load_status([directory / "__init__.py"], [_failure(directory, "boom")])

        _write_load_status([directory / "__init__.py"], [])

        assert _load_failure(read_load_status(), directory) is None

    def test_non_load_errors_are_not_recorded_as_load_failures(self, tmp_path, config_dir):
        """An extension whose event handler raised did load; saying otherwise
        would send someone hunting an import error that does not exist."""
        directory = _extension(tmp_path)
        runtime_error = ExtensionError(
            extension_path=str(directory / "__init__.py"), event="session_start", error="later"
        )

        _write_load_status([directory / "__init__.py"], [runtime_error])

        assert _load_failure(read_load_status(), directory) is None

    def test_an_unreadable_status_file_is_not_fatal(self, tmp_path, config_dir):
        config_dir.mkdir(parents=True)
        (config_dir / "extension_load_status.json").write_text("{ not json")

        assert read_load_status() == {}


class TestReporting:
    def test_the_recorded_reason_is_what_doctor_reports(self, tmp_path, config_dir):
        directory = _extension(tmp_path)
        _write_load_status(
            [directory / "__init__.py"],
            [_failure(directory, "ModuleNotFoundError: No module named 'tau.builtins.nope'")],
        )

        message = _load_failure(read_load_status(), directory)

        assert message is not None
        assert "No module named" in message

    def test_an_extension_never_loaded_here_is_not_called_broken(self, tmp_path):
        """A fresh checkout has no records. Reporting every extension as failed
        would make a healthy clone look ill."""
        directory = _extension(tmp_path)

        assert _load_failure({}, directory) is None

    def test_a_malformed_record_is_ignored(self, tmp_path):
        directory = _extension(tmp_path)

        assert _load_failure({str(directory.resolve()): "not-a-dict"}, directory) is None

    def test_a_failure_with_no_message_still_reports(self, tmp_path):
        directory = _extension(tmp_path)

        message = _load_failure({str(directory.resolve()): {"ok": False}}, directory)

        assert message is not None
        assert "unknown error" in message


class TestDoctorSection:
    def test_a_failed_extension_appears_in_the_section(self, tmp_path, config_dir, monkeypatch):
        """The whole point: the run that reported an error is the run doctor
        should tell you about."""
        from tau.console.commands.doctor import _check_extensions

        directory = _extension(tmp_path, "rlm")
        (directory / "manifest.json").write_text(json.dumps({"tau": {"name": "RLM"}}))
        _write_load_status(
            [directory / "__init__.py"], [_failure(directory, "ModuleNotFoundError: nope")]
        )

        settings = type("SM", (), {"is_extensions_enabled": lambda self: True})()
        with (
            patch("tau.console.commands.doctor._check_dangling_entries", return_value=[]),
            patch(
                "tau.settings.paths.get_extensions_dir",
                lambda cwd=None: tmp_path / ".tau" / "extensions" if cwd else tmp_path / "none",
            ),
        ):
            section = _check_extensions(settings, tmp_path)

        statuses = [(check.status, check.detail) for check in section.results]
        assert any(status == "fail" and "failed to load" in detail for status, detail in statuses)

    def test_a_healthy_project_still_reports_no_issues(self, tmp_path, config_dir, monkeypatch):
        from tau.console.commands.doctor import _check_extensions

        directory = _extension(tmp_path, "fine")
        (directory / "manifest.json").write_text(json.dumps({"tau": {"name": "Fine"}}))
        _write_load_status([directory / "__init__.py"], [])

        settings = type("SM", (), {"is_extensions_enabled": lambda self: True})()
        with (
            patch("tau.console.commands.doctor._check_dangling_entries", return_value=[]),
            patch(
                "tau.settings.paths.get_extensions_dir",
                lambda cwd=None: tmp_path / ".tau" / "extensions" if cwd else tmp_path / "none",
            ),
        ):
            section = _check_extensions(settings, tmp_path)

        assert all(check.status == "pass" for check in section.results)
