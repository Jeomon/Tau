from __future__ import annotations

# ruff: noqa: E402, I001

import asyncio
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

_EXTENSIONS_DIR = Path(__file__).parents[1] / "examples" / "extensions"
if str(_EXTENSIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_EXTENSIONS_DIR))

from subagents.agents import _parse_md, load_agent_types
from subagents.manager import SubagentManager
from subagents.memory import build_memory_block, resolve_memory_dir
from subagents.scheduler import SubagentScheduler, parse_schedule
from subagents.service import _select_tools
from subagents.skills import build_skills_block
from subagents.types import AgentRecord, AgentStatus
from subagents.ui import AgentWidget, ConversationViewer
from subagents.worktree import create_worktree, finalize_worktree
from subagents.tool import AgentTool
from tau.tui.input import KeyEvent


def test_builtin_markdown_profiles_load() -> None:
    profiles = load_agent_types(Path.cwd())

    assert {
        "general-purpose",
        "scout",
        "researcher",
        "planner",
        "worker",
        "reviewer",
        "oracle",
    } <= profiles.keys()
    assert profiles["general-purpose"].system_prompt == ""
    assert profiles["scout"].tools == ["read", "grep", "glob", "ls"]


def test_profile_parses_memory_skills_and_tool_denylist(tmp_path: Path) -> None:
    profile = tmp_path / "auditor.md"
    profile.write_text(
        "---\n"
        "description: Audit changes\n"
        "tools: all\n"
        "disallowed_tools: write, terminal\n"
        "skills: security, testing\n"
        "memory: project\n"
        "isolation: worktree\n"
        "---\n"
        "Audit carefully.\n",
        encoding="utf-8",
    )

    parsed = _parse_md(profile)

    assert parsed is not None
    assert parsed.disallowed_tools == ["write", "terminal"]
    assert parsed.skills == ["security", "testing"]
    assert parsed.memory == "project"
    assert parsed.isolation == "worktree"
    selected = {tool.name for tool in _select_tools(parsed.tools, parsed.disallowed_tools)}
    assert "read" in selected
    assert "write" not in selected
    assert "terminal" not in selected


def test_memory_is_injected_and_unsafe_names_are_rejected(tmp_path: Path) -> None:
    block = build_memory_block(
        agent_name="reviewer",
        scope="project",
        cwd=tmp_path,
        writable=True,
    )
    memory_dir = tmp_path / ".tau" / "subagents" / "memory" / "reviewer"
    assert memory_dir.is_dir()
    assert "No MEMORY.md exists yet" in block

    (memory_dir / "MEMORY.md").write_text("Remember this.\n", encoding="utf-8")
    readonly = build_memory_block(
        agent_name="reviewer",
        scope="project",
        cwd=tmp_path,
        writable=False,
    )
    assert "Remember this." in readonly
    assert "read-only" in readonly

    with pytest.raises(ValueError, match="Unsafe agent name"):
        resolve_memory_dir("../escape", "project", tmp_path)


def test_named_skills_are_preloaded(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".tau" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Review rules\n---\nFollow project review rules.\n",
        encoding="utf-8",
    )

    block = build_skills_block(["review"], tmp_path)

    assert "## review" in block
    assert "Follow project review rules." in block


def test_schedule_formats_and_persistence(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    assert parse_schedule("5m", now) == ("interval", now + 300, 300)
    assert parse_schedule("+10s", now) == ("once", now + 10, None)
    _, next_run, _ = parse_schedule("0 0 9 * * 1", now)
    next_dt = datetime.fromtimestamp(next_run, UTC)
    assert next_dt.weekday() == 0
    assert next_dt.hour == 9

    class Manager:
        async def rpc_spawn(self, request):
            self.request = request
            return {"success": True, "data": {}}

    manager = Manager()
    scheduler = SubagentScheduler(manager, tmp_path, "session")
    job = scheduler.add("+1s", {"prompt": "x", "description": "scheduled"})
    restored = SubagentScheduler(manager, tmp_path, "session")
    assert restored.list_jobs()[0].id == job.id

    restored.list_jobs()[0].next_run = 0
    asyncio.run(restored._fire(restored.list_jobs()[0]))
    assert manager.request["run_in_background"] is True
    assert restored.list_jobs() == []


def test_record_persistence_and_lifecycle_rpc(tmp_path: Path) -> None:
    output = tmp_path / "output"
    manager = SubagentManager(tmp_path, output)
    record = AgentRecord(
        id="deadbeef",
        agent_type="worker",
        description="test",
        prompt="task",
        status=AgentStatus.COMPLETED,
        model=None,
        max_turns=None,
        run_in_background=False,
        output_file=output / "deadbeef" / "session.jsonl",
    )
    manager._records[record.id] = record
    manager._persist_record(record)

    restored = SubagentManager(tmp_path, output)
    assert restored.get_record("deadbeef") is not None
    assert restored.rpc_ping() == {
        "success": True,
        "data": {"protocol_version": 1},
        "error": None,
    }
    assert restored.rpc_stop("missing")["success"] is False

    events: list[tuple[str, str]] = []
    unsubscribe = manager.subscribe(lambda kind, payload: events.append((kind, payload["id"])))
    asyncio.run(manager._emit("completed", record))
    unsubscribe()
    assert events == [("completed", "deadbeef")]


def test_scheduling_field_is_removed_when_disabled(tmp_path: Path) -> None:
    manager = SubagentManager(tmp_path, tmp_path / "output")

    enabled_schema = AgentTool(manager, scheduling_enabled=True).schema.model_json_schema()
    disabled_schema = AgentTool(manager, scheduling_enabled=False).schema.model_json_schema()

    assert "schedule" in enabled_schema["properties"]
    assert "schedule" not in disabled_schema["properties"]


def test_model_scope_rejects_disallowed_override(tmp_path: Path) -> None:
    class Model:
        id = "parent"

    class LLM:
        model = Model()
        provider_id = "test"

    manager = SubagentManager(
        tmp_path,
        tmp_path / "output",
        scope_models=True,
    )

    async def spawn() -> None:
        await manager.spawn(
            prompt="x",
            description="x",
            model="other/model",
            llm=LLM(),
            enabled_models=["test/*"],
        )

    with pytest.raises(ValueError, match="outside enabled_models"):
        asyncio.run(spawn())


def test_live_widget_and_conversation_viewer() -> None:
    record = AgentRecord(
        id="abc123",
        agent_type="scout",
        description="Inspect auth",
        prompt="x",
        status=AgentStatus.RUNNING,
        model=None,
        max_turns=None,
        run_in_background=True,
    )

    class Manager:
        @staticmethod
        def list_records():
            return [record]

    assert any("scout" in line for line in AgentWidget(Manager()).render(100))
    closed: list[None] = []
    viewer = ConversationViewer(record, closed.append)
    assert any("transcript not available" in line for line in viewer.render(100))
    viewer.handle_input(KeyEvent(key="escape"))
    assert closed == [None]


def test_worktree_changes_are_committed_to_agent_branch(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            text=True,
            capture_output=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "initial")

    agent_id = uuid.uuid4().hex[:8]

    async def exercise() -> None:
        info = await create_worktree(repository, agent_id)
        (info.path / "change.txt").write_text("changed\n", encoding="utf-8")
        changed, error = await finalize_worktree(info, "test change")
        assert changed is True
        assert error is None
        assert not info.path.exists()

    asyncio.run(exercise())
    branch = f"tau-agent-{agent_id}"
    assert branch in git("branch", "--list", branch).stdout
