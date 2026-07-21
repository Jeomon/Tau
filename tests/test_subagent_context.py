from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.session.manager import SessionManager
from tests.ext_loader import load_extension

# Loaded as a package, like tau's loader — `agents` is relative inside it, not
# a bare global name shared with the workflow extension.
_PKG = load_extension("subagent").__name__
subagent_tool = importlib.import_module(f"{_PKG}.subagent_tool")
AgentConfig = importlib.import_module(f"{_PKG}.agents").AgentConfig



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


async def _fake_run_embedded_agent(captured: dict, **kwargs):
    captured["initial_messages"] = kwargs.get("initial_messages")
    return True, "done", {"turns": 1, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}


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


@pytest.mark.anyio
class TestRunSingleAgentContext:
    """context_mode resolution inside run_single_agent: 'fresh' passes no
    initial_messages to run_embedded_agent; 'fork' loads them from the
    parent's session file via load_fork_context()."""

    async def test_fresh_context_has_no_initial_messages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            subagent_tool,
            "run_embedded_agent",
            lambda **kwargs: _fake_run_embedded_agent(captured, **kwargs),
        )

        await subagent_tool.run_single_agent(
            default_cwd=tmp_path,
            agents=[_agent(context=None)],
            agent_name="worker",
            task="do x",
            cwd=None,
            step=None,
            signal=None,
            on_update=None,
            main_model=None,
            requested_context="fresh",
            parent_session=None,
        )

        assert captured["initial_messages"] is None

    async def test_fork_context_loads_parent_session_messages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = tmp_path / "sessions"
        captured: dict = {}

        def _fake_load_fork_context(cwd: Path, session_id: str, sdir: Path) -> list[str]:
            captured["load_fork_context_args"] = (cwd, session_id, sdir)
            return ["fake-message"]

        monkeypatch.setattr(subagent_tool, "load_fork_context", _fake_load_fork_context)
        monkeypatch.setattr(
            subagent_tool,
            "run_embedded_agent",
            lambda **kwargs: _fake_run_embedded_agent(captured, **kwargs),
        )

        await subagent_tool.run_single_agent(
            default_cwd=tmp_path,
            agents=[_agent(context=None)],
            agent_name="worker",
            task="do x",
            cwd=None,
            step=None,
            signal=None,
            on_update=None,
            main_model=None,
            requested_context="fork",
            parent_session=("abc123", session_dir),
        )

        assert captured["initial_messages"] == ["fake-message"]
        assert captured["load_fork_context_args"][1] == "abc123"
        assert captured["load_fork_context_args"][2] == session_dir


@pytest.mark.anyio
class TestRunSingleAgentForkFailsFast:
    async def test_fork_without_parent_session_errors_without_spawning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(**kwargs):
            raise AssertionError("must not run an embedded agent when fork has no parent session")

        monkeypatch.setattr(subagent_tool, "run_embedded_agent", _boom)

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
    default, which beats "fresh". Verified by checking whether
    initial_messages ends up populated (i.e. fork was resolved) instead of
    spawning a real subagent process."""

    async def _resolved_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        agent,
        requested_context,
        parent_session,
    ) -> bool:
        """Returns whether the run resolved to fork (initial_messages populated)."""
        captured: dict = {}

        def _fake_load_fork_context(cwd: Path, session_id: str, sdir: Path) -> list[str]:
            return ["fake-message"]

        monkeypatch.setattr(subagent_tool, "load_fork_context", _fake_load_fork_context)
        monkeypatch.setattr(
            subagent_tool,
            "run_embedded_agent",
            lambda **kwargs: _fake_run_embedded_agent(captured, **kwargs),
        )

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
        return captured["initial_messages"] is not None

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
        agents = importlib.import_module(f"{_PKG}.agents")
        _BUILTIN_AGENTS_DIR = agents._BUILTIN_AGENTS_DIR
        _load_agents_from_dir = agents._load_agents_from_dir

        return _load_agents_from_dir(_BUILTIN_AGENTS_DIR, "builtin")

    def test_planner_worker_oracle_default_to_fork(self) -> None:
        by_name = {a.name: a for a in self._builtin_agents()}

        for name in ("planner", "worker", "oracle"):
            assert by_name[name].context == "fork", name

    def test_other_builtins_have_no_context_default(self) -> None:
        by_name = {a.name: a for a in self._builtin_agents()}

        for name in ("scout", "researcher", "context-builder", "reviewer", "delegate"):
            assert by_name[name].context is None, name


class TestMarkdownRendering:
    """Subagent results render markdown the same way web_fetch does — see
    subagent_tool._render_markdown_body."""

    def test_single_result_renders_markdown_headers_and_lists(self) -> None:
        from tau.tui.theme import MessageTheme

        theme = MessageTheme()
        md_text = "# Plan\n\n- step one\n- step two"
        opts = SimpleNamespace(
            theme=theme,
            is_error=False,
            expanded=False,
            metadata={
                "mode": "spawn",
                "results": [
                    {"agent": "planner", "source": "builtin", "status": "ok", "usage": "1 turn"}
                ],
            },
        )

        out = subagent_tool._render_result(md_text, opts)
        joined = "\n".join(out)

        assert "# Plan" not in joined  # markdown syntax consumed, not shown raw
        assert "Plan" in joined
        assert "step one" in joined
        assert "step two" in joined

    def test_no_theme_falls_back_to_raw_lines(self) -> None:
        opts = SimpleNamespace(
            theme=None,
            is_error=False,
            expanded=False,
            metadata={
                "mode": "spawn",
                "results": [{"agent": "planner", "source": "builtin", "status": "ok"}],
            },
        )

        out = subagent_tool._render_result("# Plan\n\n- step one", opts)
        joined = "\n".join(out)

        # No theme (e.g. non-interactive mode) -> raw content, unrendered.
        assert "# Plan" in joined

    def test_error_result_is_not_markdown_rendered(self) -> None:
        from tau.tui.theme import MessageTheme

        theme = MessageTheme()
        opts = SimpleNamespace(
            theme=theme,
            is_error=True,
            expanded=False,
            metadata={
                "mode": "spawn",
                "results": [{"agent": "planner", "source": "builtin", "status": "error"}],
            },
        )

        out = subagent_tool._render_result("# Boom\n\nsomething broke", opts)
        joined = "\n".join(out)

        assert "# Boom" in joined
