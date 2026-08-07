"""`/trust` — inspecting and changing a project's trust decision mid-session.

Trust decides whether a project's own `.tau/` settings, extensions and context
files are loaded. It was asked once at startup and never surfaced again, so a
decision could only be revisited by editing `~/.tau/trust.json` by hand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.interactive.commands import trust as cmd_trust


class _Settings:
    def __init__(self, trusted: bool = False) -> None:
        self._trusted = trusted

    def is_project_trusted(self) -> bool:
        return self._trusted

    def set_project_trusted(self, trusted: bool) -> None:
        self._trusted = trusted


class _Ctx:
    """The slice of CommandContext /trust touches, plus captured output."""

    def __init__(self, cwd: Path, trusted: bool = False) -> None:
        self.messages: list[str] = []
        self.reloads = 0

        async def _reload() -> None:
            self.reloads += 1

        self.runtime = SimpleNamespace(
            settings_manager=_Settings(trusted),
            session_manager=SimpleNamespace(cwd=cwd),
            reload_extensions=_reload,
        )

    def notify(self, message: str) -> None:
        self.messages.append(message)

    @property
    def last(self) -> str:
        return self.messages[-1]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A trust store isolated from the real ~/.tau/trust.json."""
    from tau.trust.manager import TrustStore

    isolated = TrustStore(config_dir=tmp_path / "config")
    monkeypatch.setattr("tau.trust.manager.trust_store", isolated)
    return isolated


def _run(ctx, *args):
    asyncio.run(cmd_trust.cmd_trust(ctx, list(args)))


class TestReporting:
    def test_it_reports_undecided_projects(self, tmp_path, store):
        ctx = _Ctx(tmp_path)

        _run(ctx)

        assert "not trusted" in ctx.last
        assert "asked again next time" in ctx.last

    def test_in_effect_and_remembered_are_reported_separately(self, tmp_path, store):
        """A session-only answer makes them differ on purpose, and a user
        needs to see which one applies next time."""
        store.set(tmp_path, False)
        ctx = _Ctx(tmp_path, trusted=True)  # granted for this session only

        _run(ctx)

        assert "overrides what is stored" in ctx.last

    def test_an_inherited_decision_names_the_directory_holding_it(self, tmp_path, store):
        child = tmp_path / "child"
        child.mkdir()
        store.set(tmp_path, True)
        ctx = _Ctx(child, trusted=True)

        _run(ctx)

        assert "inherited from" in ctx.last


class TestChanging:
    def test_yes_trusts_and_remembers(self, tmp_path, store):
        ctx = _Ctx(tmp_path)

        _run(ctx, "yes")

        assert ctx.runtime.settings_manager.is_project_trusted() is True
        assert store.get(tmp_path) is True
        assert "remembered" in ctx.last

    def test_session_trusts_without_remembering(self, tmp_path, store):
        ctx = _Ctx(tmp_path)

        _run(ctx, "session")

        assert ctx.runtime.settings_manager.is_project_trusted() is True
        assert store.get(tmp_path) is None
        assert "this session only" in ctx.last

    def test_no_untrusts_and_remembers(self, tmp_path, store):
        ctx = _Ctx(tmp_path, trusted=True)

        _run(ctx, "no")

        assert ctx.runtime.settings_manager.is_project_trusted() is False
        assert store.get(tmp_path) is False

    def test_forget_clears_the_stored_answer_but_not_the_session(self, tmp_path, store):
        store.set(tmp_path, True)
        ctx = _Ctx(tmp_path, trusted=True)

        _run(ctx, "forget")

        assert store.get(tmp_path) is None
        assert ctx.runtime.settings_manager.is_project_trusted() is True
        assert "Still trusted for this session" in ctx.last

    def test_an_unknown_option_is_rejected_without_changing_anything(self, tmp_path, store):
        ctx = _Ctx(tmp_path)

        _run(ctx, "maybe")

        assert "Unknown option" in ctx.last
        assert store.get(tmp_path) is None
        assert ctx.runtime.settings_manager.is_project_trusted() is False


class TestReload:
    def test_granting_trust_reloads_so_project_config_takes_effect(self, tmp_path, store):
        """Project settings were skipped at startup; extensions and context
        files are read while building the session, so they need a reload."""
        ctx = _Ctx(tmp_path)

        _run(ctx, "yes")

        assert ctx.reloads == 1
        assert "reloaded" in ctx.last

    def test_re_trusting_an_already_trusted_project_does_not_reload(self, tmp_path, store):
        ctx = _Ctx(tmp_path, trusted=True)

        _run(ctx, "yes")

        assert ctx.reloads == 0

    def test_withdrawing_trust_does_not_reload(self, tmp_path, store):
        ctx = _Ctx(tmp_path, trusted=True)

        _run(ctx, "no")

        assert ctx.reloads == 0


class TestSurface:
    def test_the_command_is_registered_with_its_options(self):
        import inspect

        from tau.modes.interactive.app import App

        source = inspect.getsource(App)

        assert 'name="trust"' in source
        assert "yes|session|no|forget" in source
