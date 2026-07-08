"""Tests for the subagent tool's context='fresh'|'fork' plumbing.

'fork' resumes the parent's current persisted session as read-only context
via `tau --resume <id> --session-dir <dir> --ephemeral` rather than creating
a new forked session file — see subagent_tool._parent_session /
_build_run_args. These tests cover that logic directly, without spawning any
real subagent process.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import tau.builtins.extensions.subagent  # noqa: F401  side effect: sys.path.insert for local imports

import subagent_tool  # type: ignore[import-not-found]
from agents import AgentConfig  # type: ignore[import-not-found]
from tau.session.manager import SessionManager


def _agent(**overrides) -> AgentConfig:
    defaults: dict = dict(
        name="worker",
        description="",
        tools=None,
        model=None,
        system_prompt="",
        source="builtin",
        file_path="worker.md",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestParentSession:
    def test_none_when_runtime_ref_is_none(self) -> None:
        assert subagent_tool._parent_session(None) is None

    def test_none_when_runtime_is_none(self) -> None:
        ref = SimpleNamespace(runtime=None)
        assert subagent_tool._parent_session(ref) is None

    def test_none_when_session_manager_missing(self) -> None:
        ref = SimpleNamespace(runtime=SimpleNamespace())
        assert subagent_tool._parent_session(ref) is None

    def test_none_when_session_not_persisted(self, tmp_path: Path) -> None:
        sm = SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=False)
        ref = SimpleNamespace(runtime=SimpleNamespace(session_manager=sm))
        assert subagent_tool._parent_session(ref) is None

    def test_returns_id_and_dir_when_persisted(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions"
        sm = SessionManager(cwd=tmp_path, session_dir=session_dir, persist=True)
        ref = SimpleNamespace(runtime=SimpleNamespace(session_manager=sm))

        result = subagent_tool._parent_session(ref)

        assert result == (sm.session_id, session_dir)


class TestBuildRunArgs:
    def test_fresh_context_has_no_resume(self, tmp_path: Path) -> None:
        args = subagent_tool._build_run_args(_agent(), "do x", tmp_path, None, None)

        assert "--resume" not in args
        assert "--session-dir" not in args
        assert "--ephemeral" in args

    def test_fork_context_resumes_parent_session(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "sessions"

        args = subagent_tool._build_run_args(
            _agent(), "do x", tmp_path, None, ("abc123", session_dir)
        )

        assert args[: args.index("--ephemeral")] == [
            "--mode",
            "json",
            "--quiet",
            "--cwd",
            str(tmp_path),
            "--resume",
            "abc123",
            "--session-dir",
            str(session_dir),
        ]
        # --ephemeral still present: the child never writes back to the
        # resumed session regardless of context mode.
        assert "--ephemeral" in args


@pytest.mark.anyio
class TestRunSingleAgentForkFailsFast:
    async def test_fork_without_parent_session_errors_without_spawning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(*args, **kwargs):
            raise AssertionError("must not spawn a process when fork has no parent session")

        monkeypatch.setattr(subagent_tool, "_run_process", _boom)

        result = await subagent_tool.run_single_agent(
            default_cwd=tmp_path,
            agents=[_agent()],
            agent_name="worker",
            task="do x",
            cwd=None,
            step=None,
            signal=None,
            on_update=None,
            main_model=None,
            requested_context="fork",
            parent_session=None,
        )

        assert result.failed
        assert "no persisted parent session" in result.error_message


@pytest.mark.anyio
class TestContextPrecedence:
    """requested_context (explicit task/run) beats the agent's own frontmatter
    default, which beats "fresh". Verified by capturing the args passed to
    _build_run_args instead of spawning a real subagent process."""

    async def _resolved_context(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, agent, requested_context, parent_session
    ) -> bool:
        """Returns whether _build_run_args was called with fork's resume args."""
        captured: dict = {}

        def _fake_build_run_args(agent, task, run_cwd, model, resolved_parent_session):
            captured["forked"] = resolved_parent_session is not None
            return ["--mode", "json"]

        async def _noop_run_process(*args, **kwargs):
            return None

        monkeypatch.setattr(subagent_tool, "_build_run_args", _fake_build_run_args)
        monkeypatch.setattr(subagent_tool, "_run_process", _noop_run_process)

        await subagent_tool.run_single_agent(
            default_cwd=tmp_path,
            agents=[agent],
            agent_name=agent.name,
            task="do x",
            cwd=None,
            step=None,
            signal=None,
            on_update=None,
            main_model=None,
            requested_context=requested_context,
            parent_session=parent_session,
        )
        return captured["forked"]

    async def test_agent_default_fork_used_when_nothing_explicit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent(name="planner", context="fork")
        forked = await self._resolved_context(
            monkeypatch,
            tmp_path,
            agent=agent,
            requested_context=None,
            parent_session=("id", tmp_path),
        )
        assert forked is True

    async def test_explicit_fresh_overrides_agent_fork_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent(name="planner", context="fork")
        forked = await self._resolved_context(
            monkeypatch,
            tmp_path,
            agent=agent,
            requested_context="fresh",
            parent_session=("id", tmp_path),
        )
        assert forked is False

    async def test_no_agent_default_and_no_explicit_is_fresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = _agent(name="scout", context=None)
        forked = await self._resolved_context(
            monkeypatch,
            tmp_path,
            agent=agent,
            requested_context=None,
            parent_session=("id", tmp_path),
        )
        assert forked is False


class TestBuiltinAgentContextDefaults:
    """planner/worker/oracle ship with context: fork in their frontmatter;
    everything else defaults to fresh (context: None). Reads the builtin
    agents directory directly (bypassing discover_agents' user/project
    tiers) so this doesn't depend on the running machine's ~/.tau/agents."""

    def _builtin_agents(self):
        from agents import _BUILTIN_AGENTS_DIR, _load_agents_from_dir  # type: ignore[import-not-found]

        return _load_agents_from_dir(_BUILTIN_AGENTS_DIR, "builtin")

    def test_planner_worker_oracle_default_to_fork(self) -> None:
        by_name = {a.name: a for a in self._builtin_agents()}

        for name in ("planner", "worker", "oracle"):
            assert by_name[name].context == "fork", name

    def test_other_builtins_have_no_context_default(self) -> None:
        by_name = {a.name: a for a in self._builtin_agents()}

        for name in ("scout", "researcher", "context-builder", "reviewer", "delegate"):
            assert by_name[name].context is None, name
