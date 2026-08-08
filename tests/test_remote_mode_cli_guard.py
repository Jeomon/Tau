"""``--mode remote`` refuses an initial prompt instead of dropping it.

Remote mode serves a session and waits; it has no initial-message path. Before
this guard ``tau --mode remote -p "do the thing"`` started a server, ignored the
prompt, and reported nothing — the work simply never happened, and the only
symptom was its absence.

The guard also has to run *before* Runtime.create, so a rejected invocation does
not spend startup building an agent it will throw away, or bind a socket it will
immediately abandon.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from tau.console.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestRemoteRejectsInitialMessages:
    def test_prompt_is_refused(self, runner):
        result = runner.invoke(cli, ["--mode", "remote", "-p", "write a file"])

        assert result.exit_code != 0
        assert "not supported with --mode remote" in result.output

    def test_file_is_refused(self, runner, tmp_path):
        context = tmp_path / "ctx.txt"
        context.write_text("some context")

        result = runner.invoke(cli, ["--mode", "remote", "--file", str(context)])

        assert result.exit_code != 0
        assert "not supported with --mode remote" in result.output

    def test_the_error_says_what_to_do_instead(self, runner):
        """An error that only says no leaves the user to guess the alternative."""
        result = runner.invoke(cli, ["--mode", "remote", "-p", "x"])

        assert "attached client" in result.output

    def test_it_fails_before_building_a_runtime(self, runner, monkeypatch):
        """A rejected run must not pay for startup it is about to discard."""
        from tau.runtime.service import Runtime

        def _fail(*_args, **_kwargs):
            raise AssertionError("Runtime.create must not run for a rejected invocation")

        monkeypatch.setattr(Runtime, "create", _fail)

        result = runner.invoke(cli, ["--mode", "remote", "-p", "x"])

        assert result.exit_code != 0


class TestOtherModesAreUnaffected:
    def test_remote_without_a_prompt_is_accepted(self, runner, monkeypatch):
        """The guard must not reject the ordinary way to start a server."""
        started: list[str] = []

        async def _fake_start(runtime, socket_path=None):
            started.append("ran")

        monkeypatch.setattr("tau.modes.remote.mode.run_remote_mode", _fake_start)

        # Runtime.create is the expensive part; stub it so this stays a unit test.
        async def _fake_create(config):
            started.append("created")
            raise SystemExit(0)

        from tau.runtime.service import Runtime

        monkeypatch.setattr(Runtime, "create", staticmethod(_fake_create))

        result = runner.invoke(cli, ["--mode", "remote"])

        assert "not supported with --mode remote" not in result.output
        assert "created" in started, "startup should have proceeded past the guard"
