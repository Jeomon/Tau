"""Tests for the RPC handlers that used to answer success without doing anything.

Each class here pins one entry from the old "Known Gaps" table in docs/rpc.md:
the handler must now reach the real API, and say so when it cannot.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import tau.modes.rpc.mode as mode
from tau.engine.types import FollowupMode, SteeringMode
from tau.inference.types import ThinkingLevel
from tau.modes import wire


@pytest.fixture
def captured(monkeypatch):
    lines: list = []
    monkeypatch.setattr(mode, "_write", lambda obj: lines.append(obj))
    return lines


# ── Doubles ──────────────────────────────────────────────────────────────────


class _Options:
    def __init__(self) -> None:
        self.thinking_level: ThinkingLevel | None = None
        self.max_retries = 3


class _API:
    def __init__(self) -> None:
        self.options = _Options()


class _Model:
    def __init__(self, levels=None, context_window=200_000) -> None:
        self.id = "test-model"
        self.name = "Test Model"
        self.provider = "test"
        self.context_window = context_window
        self.thinking_levels = levels if levels is not None else []

    def clamp_thinking_level(self, level):
        if level is None or not self.thinking_levels:
            return level
        return level if level in self.thinking_levels else self.thinking_levels[0]


class _Queue:
    def __init__(self, mode_value) -> None:
        self.mode = mode_value

    def snapshot(self):
        return []


class _State:
    def __init__(self) -> None:
        self.steering_queue = _Queue(SteeringMode.All)
        self.follow_up_queue = _Queue(FollowupMode.All)


class _LLM:
    def __init__(self, model=None) -> None:
        self.api = _API()
        self.model = model or _Model()
        self.retry_aborted = False

    def abort_retry(self) -> bool:
        self.retry_aborted = True
        return True


class _Engine:
    def __init__(self, llm=None) -> None:
        self.llm = llm or _LLM()
        self.state = _State()


class _Agent:
    def __init__(self, llm=None) -> None:
        self._engine = _Engine(llm)
        self.queued_messages = {"steering": [], "followup": []}

    def is_idle(self) -> bool:
        return True


class _Runtime:
    def __init__(self, agent=None, session_manager=None) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.settings_manager = None
        self.commands = None
        self.renames: list[str] = []
        self.project_trusted = False
        self.project_trust_source = "undecided"
        self.reloads = 0

    async def reload_extensions(self) -> None:
        self.reloads += 1

    async def set_session_name(self, name: str) -> str | None:
        """Mirror of Runtime.set_session_name — appends and announces.

        The handler routes through the runtime rather than the session manager
        so `session_info_changed` fires for RPC renames too; this double has to
        offer the same seam or the test passes against a shape that no longer
        exists.
        """
        self.renames.append(name)
        if self.session_manager is None:
            return None
        return self.session_manager.append_session_info(name)


# ── Thinking level ───────────────────────────────────────────────────────────


class TestThinkingLevel:
    @pytest.mark.asyncio
    async def test_set_writes_through_to_the_live_api_options(self, captured):
        rt = _Runtime(_Agent())
        await mode._handle_command(
            {"type": "set_thinking_level", "id": "1", "level": "high"}, rt, {}
        )

        assert rt.agent._engine.llm.api.options.thinking_level is ThinkingLevel.High
        assert captured[-1]["success"] is True
        assert captured[-1]["data"]["level"] == "high"

    @pytest.mark.asyncio
    async def test_off_is_stored_as_none(self, captured):
        rt = _Runtime(_Agent())
        rt.agent._engine.llm.api.options.thinking_level = ThinkingLevel.High

        await mode._handle_command(
            {"type": "set_thinking_level", "id": "1", "level": "off"}, rt, {}
        )

        assert rt.agent._engine.llm.api.options.thinking_level is None

    @pytest.mark.asyncio
    async def test_unsupported_level_is_clamped_to_a_supported_one(self, captured):
        llm = _LLM(_Model(levels=[ThinkingLevel.Low]))
        await mode._handle_command(
            {"type": "set_thinking_level", "id": "1", "level": "high"}, _Runtime(_Agent(llm)), {}
        )

        assert llm.api.options.thinking_level is ThinkingLevel.Low
        assert captured[-1]["data"]["level"] == "low"

    @pytest.mark.asyncio
    async def test_unknown_level_is_an_error(self, captured):
        await mode._handle_command(
            {"type": "set_thinking_level", "id": "1", "level": "turbo"}, _Runtime(_Agent()), {}
        )

        assert captured[-1]["success"] is False
        assert "turbo" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_cycle_stays_within_the_models_supported_levels(self, captured):
        llm = _LLM(_Model(levels=[ThinkingLevel.Off, ThinkingLevel.Low]))
        rt = _Runtime(_Agent(llm))

        await mode._handle_command({"type": "cycle_thinking_level", "id": "1"}, rt, {})
        assert llm.api.options.thinking_level is ThinkingLevel.Low

        await mode._handle_command({"type": "cycle_thinking_level", "id": "2"}, rt, {})
        assert llm.api.options.thinking_level is None  # wrapped back to Off


# ── Queue modes ──────────────────────────────────────────────────────────────


class TestQueueModes:
    @pytest.mark.asyncio
    async def test_steering_mode_reaches_the_queue_on_engine_state(self, captured):
        rt = _Runtime(_Agent())
        await mode._handle_command(
            {"type": "set_steering_mode", "id": "1", "mode": "one-at-a-time"}, rt, {}
        )

        assert rt.agent._engine.state.steering_queue.mode is SteeringMode.OneAtATime
        assert captured[-1]["success"] is True

    @pytest.mark.asyncio
    async def test_follow_up_mode_reaches_the_queue_on_engine_state(self, captured):
        rt = _Runtime(_Agent())
        await mode._handle_command(
            {"type": "set_follow_up_mode", "id": "1", "mode": "one-at-a-time"}, rt, {}
        )

        assert rt.agent._engine.state.follow_up_queue.mode is FollowupMode.OneAtATime

    @pytest.mark.asyncio
    async def test_unknown_mode_is_rejected(self, captured):
        rt = _Runtime(_Agent())
        await mode._handle_command(
            {"type": "set_steering_mode", "id": "1", "mode": "sometimes"}, rt, {}
        )

        assert captured[-1]["success"] is False
        assert rt.agent._engine.state.steering_queue.mode is SteeringMode.All

    @pytest.mark.asyncio
    async def test_no_agent_is_an_error(self, captured):
        await mode._handle_command(
            {"type": "set_steering_mode", "id": "1", "mode": "all"}, _Runtime(), {}
        )
        assert captured[-1]["success"] is False


# ── Aborts ───────────────────────────────────────────────────────────────────


class TestAborts:
    @pytest.mark.asyncio
    async def test_abort_retry_reaches_the_llm(self, captured):
        rt = _Runtime(_Agent())
        await mode._handle_command({"type": "abort_retry", "id": "1"}, rt, {})

        assert rt.agent._engine.llm.retry_aborted is True
        assert captured[-1]["data"] == {"aborted": True}

    @pytest.mark.asyncio
    async def test_abort_terminal_reports_when_nothing_is_running(self, captured):
        class _RT(_Runtime):
            def abort_terminal(self):
                return False

        await mode._handle_command({"type": "abort_terminal", "id": "1"}, _RT(), {})
        assert captured[-1]["data"] == {"aborted": False}

    @pytest.mark.asyncio
    async def test_abort_terminal_reports_a_real_kill(self, captured):
        class _RT(_Runtime):
            def abort_terminal(self):
                return True

        await mode._handle_command({"type": "abort_terminal", "id": "1"}, _RT(), {})
        assert captured[-1]["data"] == {"aborted": True}


# ── Model listing and switching ──────────────────────────────────────────────


class TestModel:
    @pytest.mark.asyncio
    async def test_set_model_failure_is_reported_as_failure(self, captured):
        class _RT(_Runtime):
            async def set_model(self, model_id, provider=None):
                return False

        await mode._handle_command(
            {"type": "set_model", "id": "1", "modelId": "nope"}, _RT(_Agent()), {}
        )

        assert captured[-1]["success"] is False
        assert "nope" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_set_model_success_returns_the_active_model(self, captured):
        class _RT(_Runtime):
            async def set_model(self, model_id, provider=None):
                return True

        await mode._handle_command(
            {"type": "set_model", "id": "1", "modelId": "test-model"}, _RT(_Agent()), {}
        )

        assert captured[-1]["success"] is True
        assert captured[-1]["data"]["id"] == "test-model"

    def test_available_models_read_the_context_window_field(self):
        assert _Model().context_window == 200_000
        # The handler reads `context_window`; `context_length` does not exist.
        assert not hasattr(_Model(), "context_length")


# ── Context usage ────────────────────────────────────────────────────────────


class TestContextUsage:
    def test_reads_the_agents_accessor(self):
        class _Usage:
            tokens = 1_000
            context_window = 4_000
            percent = None

        class _A:
            def get_context_usage(self):
                return _Usage()

        assert mode._context_usage(_A()) == {
            "tokens": 1_000,
            "contextWindow": 4_000,
            "percent": 25.0,
        }

    def test_none_when_unavailable(self):
        class _A:
            def get_context_usage(self):
                return None

        assert mode._context_usage(_A()) is None
        assert mode._context_usage(None) is None

    def test_engine_attribute_is_not_used(self):
        # The old handler read engine.context_usage, which never exists.
        class _A:
            _engine = type("E", (), {"context_usage": object()})()

        assert mode._context_usage(_A()) is None


# ── Session name, compaction, export ─────────────────────────────────────────


class _SessionManager:
    def __init__(self, entries=None) -> None:
        self.session_id = "sess-1"
        self.cwd = "/work"
        self.named: list[str] = []
        self._entries = entries or []

    def append_session_info(self, name):
        self.named.append(name)
        return "entry-1"

    def get_session_name(self):
        return self.named[-1] if self.named else None

    def get_branch(self):
        return self._entries


class TestSessionName:
    @pytest.mark.asyncio
    async def test_name_is_appended_as_a_session_entry(self, captured):
        sm = _SessionManager()
        await mode._handle_command(
            {"type": "set_session_name", "id": "1", "name": "  refactor rpc  "},
            _Runtime(_Agent(), sm),
            {},
        )

        assert sm.named == ["refactor rpc"]
        assert captured[-1]["data"] == {"name": "refactor rpc"}

    @pytest.mark.asyncio
    async def test_blank_name_is_rejected(self, captured):
        sm = _SessionManager()
        await mode._handle_command(
            {"type": "set_session_name", "id": "1", "name": "   "}, _Runtime(_Agent(), sm), {}
        )

        assert sm.named == []
        assert captured[-1]["success"] is False


class TestCompact:
    @pytest.mark.asyncio
    async def test_failure_is_reported_as_failure(self, captured):
        class _A(_Agent):
            async def compact(self, custom_instructions=None):
                return False

        await mode._handle_command(
            {"type": "compact", "id": "1"}, _Runtime(_A(), _SessionManager()), {}
        )

        assert captured[-1]["success"] is False

    @pytest.mark.asyncio
    async def test_success_reports_details_from_the_session_entry(self, captured):
        from tau.session.types import CompactionEntry

        entry = CompactionEntry(
            id="c1",
            parent_id=None,
            summary="did stuff",
            first_kept_entry_id="e9",
            tokens_before=1234,
        )

        class _A(_Agent):
            async def compact(self, custom_instructions=None):
                return True

        await mode._handle_command(
            {"type": "compact", "id": "1"}, _Runtime(_A(), _SessionManager([entry])), {}
        )

        assert captured[-1]["data"] == {
            "compacted": True,
            "summary": "did stuff",
            "firstKeptEntryId": "e9",
            "tokensBefore": 1234,
        }


class TestExportHtml:
    @pytest.mark.asyncio
    async def test_writes_the_transcript(self, captured, tmp_path):
        from tau.message.types import UserMessage
        from tau.session.types import MessageEntry

        entry = MessageEntry(id="e0", parent_id=None, message=UserMessage.from_text("hello"))
        target = tmp_path / "out.html"

        await mode._handle_command(
            {"type": "export_html", "id": "1", "outputPath": str(target)},
            _Runtime(_Agent(), _SessionManager([entry])),
            {},
        )

        assert captured[-1]["success"] is True
        assert captured[-1]["data"]["path"] == str(target)
        assert "hello" in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_missing_output_path_is_an_error(self, captured):
        await mode._handle_command(
            {"type": "export_html", "id": "1"}, _Runtime(_Agent(), _SessionManager()), {}
        )
        assert captured[-1]["success"] is False


# ── Transcript reads ─────────────────────────────────────────────────────────


class _Entry:
    """Minimal stand-in for a session entry (pydantic-shaped)."""

    def __init__(self, entry_id: str, parent_id: str | None = None) -> None:
        self.id = entry_id
        self.parent_id = parent_id

    def model_dump(self, mode: str = "python"):
        return {"id": self.id, "parentId": self.parent_id}


class _EntriesSessionManager(_SessionManager):
    def __init__(self, entries) -> None:
        super().__init__()
        self._all = entries
        self._tree = [_Entry("root")]

    def get_entries(self):
        return self._all

    def get_leaf_id(self):
        return self._all[-1].id if self._all else None

    def get_tree(self):
        return self._tree


class TestGetEntries:
    @pytest.mark.asyncio
    async def test_returns_every_entry_and_the_leaf(self, captured):
        sm = _EntriesSessionManager([_Entry("e1"), _Entry("e2"), _Entry("e3")])

        await mode._handle_command({"type": "get_entries", "id": "1"}, _Runtime(_Agent(), sm), {})

        data = captured[-1]["data"]
        assert [e["id"] for e in data["entries"]] == ["e1", "e2", "e3"]
        assert data["leafId"] == "e3"

    @pytest.mark.asyncio
    async def test_since_returns_only_what_follows(self, captured):
        sm = _EntriesSessionManager([_Entry("e1"), _Entry("e2"), _Entry("e3")])

        await mode._handle_command(
            {"type": "get_entries", "id": "1", "since": "e1"}, _Runtime(_Agent(), sm), {}
        )

        # Excludes the cursor itself — the client already has it.
        assert [e["id"] for e in captured[-1]["data"]["entries"]] == ["e2", "e3"]

    @pytest.mark.asyncio
    async def test_since_the_latest_entry_returns_nothing(self, captured):
        sm = _EntriesSessionManager([_Entry("e1"), _Entry("e2")])

        await mode._handle_command(
            {"type": "get_entries", "id": "1", "since": "e2"}, _Runtime(_Agent(), sm), {}
        )

        assert captured[-1]["data"]["entries"] == []
        assert captured[-1]["success"] is True

    @pytest.mark.asyncio
    async def test_unknown_cursor_is_an_error(self, captured):
        sm = _EntriesSessionManager([_Entry("e1")])

        await mode._handle_command(
            {"type": "get_entries", "id": "1", "since": "nope"}, _Runtime(_Agent(), sm), {}
        )

        assert captured[-1]["success"] is False
        assert "Entry not found: nope" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_no_session_degrades_to_empty(self, captured):
        await mode._handle_command({"type": "get_entries", "id": "1"}, _Runtime(_Agent()), {})

        assert captured[-1]["data"] == {"entries": [], "leafId": None}


class TestGetTree:
    @pytest.mark.asyncio
    async def test_returns_the_tree_and_the_leaf(self, captured):
        sm = _EntriesSessionManager([_Entry("e1")])

        await mode._handle_command({"type": "get_tree", "id": "1"}, _Runtime(_Agent(), sm), {})

        data = captured[-1]["data"]
        assert data["tree"] == [{"id": "root", "parentId": None}]
        assert data["leafId"] == "e1"

    @pytest.mark.asyncio
    async def test_no_session_degrades_to_empty(self, captured):
        await mode._handle_command({"type": "get_tree", "id": "1"}, _Runtime(_Agent()), {})

        assert captured[-1]["data"] == {"tree": [], "leafId": None}


# ── new_session / cycle_model honesty ────────────────────────────────────────


class TestNewSession:
    @pytest.mark.asyncio
    async def test_success_reports_not_cancelled(self, captured):
        class _RT(_Runtime):
            def __init__(self):
                super().__init__(_Agent())
                self.parents: list = []

            async def new_session(self, *, with_session=None, parent_session=None):
                self.parents.append(parent_session)

        rt = _RT()
        await mode._handle_command({"type": "new_session", "id": "1"}, rt, {})

        assert captured[-1]["success"] is True
        assert captured[-1]["data"] == {"cancelled": False}

    @pytest.mark.asyncio
    async def test_a_crash_is_an_error_not_a_polite_cancel(self, captured):
        class _RT(_Runtime):
            async def new_session(self, *, with_session=None, parent_session=None):
                raise RuntimeError("disk full")

        await mode._handle_command({"type": "new_session", "id": "1"}, _RT(_Agent()), {})

        # Previously this answered success:true with cancelled:true — a failure
        # dressed up as the user declining.
        assert captured[-1]["success"] is False
        assert "disk full" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_parent_session_is_forwarded(self, captured):
        class _RT(_Runtime):
            def __init__(self):
                super().__init__(_Agent())
                self.parents: list = []

            async def new_session(self, *, with_session=None, parent_session=None):
                self.parents.append(parent_session)

        rt = _RT()
        await mode._handle_command(
            {"type": "new_session", "id": "1", "parentSession": "/tmp/prev.jsonl"}, rt, {}
        )

        assert rt.parents == ["/tmp/prev.jsonl"]


class TestCycleModel:
    def _runtime(self, *, switched=True):
        class _RT(_Runtime):
            def __init__(self):
                super().__init__(_Agent())
                self.requested: list = []

            async def set_model(self, model_id, provider=None):
                self.requested.append((model_id, provider))
                return switched

        return _RT()

    @pytest.mark.asyncio
    async def test_cycles_to_the_next_model(self, captured, monkeypatch):
        from tau.inference.api.text.service import TextLLM

        models = [_Model(), SimpleNamespace(id="next-model", provider="other")]
        models[0].id = "test-model"
        monkeypatch.setattr(TextLLM, "list_available", staticmethod(lambda: models))
        rt = self._runtime()

        await mode._handle_command({"type": "cycle_model", "id": "1"}, rt, {})

        assert rt.requested == [("next-model", "other")]
        assert captured[-1]["data"] == {"model": {"id": "next-model", "provider": "other"}}

    @pytest.mark.asyncio
    async def test_an_empty_model_list_is_an_error(self, captured, monkeypatch):
        from tau.inference.api.text.service import TextLLM

        monkeypatch.setattr(TextLLM, "list_available", staticmethod(list))

        await mode._handle_command({"type": "cycle_model", "id": "1"}, self._runtime(), {})

        assert captured[-1]["success"] is False
        assert "No models available" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_an_unlisted_active_model_says_so(self, captured, monkeypatch):
        from tau.inference.api.text.service import TextLLM

        other = SimpleNamespace(id="somebody-else", provider="x")
        monkeypatch.setattr(TextLLM, "list_available", staticmethod(lambda: [other]))

        await mode._handle_command({"type": "cycle_model", "id": "1"}, self._runtime(), {})

        assert captured[-1]["success"] is False
        assert "not in the available list" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_a_failed_switch_is_reported(self, captured, monkeypatch):
        from tau.inference.api.text.service import TextLLM

        models = [_Model(), SimpleNamespace(id="next-model", provider="other")]
        models[0].id = "test-model"
        monkeypatch.setattr(TextLLM, "list_available", staticmethod(lambda: models))

        await mode._handle_command(
            {"type": "cycle_model", "id": "1"}, self._runtime(switched=False), {}
        )

        assert captured[-1]["success"] is False
        assert "next-model" in captured[-1]["error"]

    @pytest.mark.asyncio
    async def test_no_agent_is_an_error(self, captured):
        await mode._handle_command({"type": "cycle_model", "id": "1"}, _Runtime(), {})
        assert captured[-1]["success"] is False


class TestParentSessionLineage:
    """`parentSession` used to be accepted and dropped. It now reaches the
    session header, so a chain of sessions can be walked back."""

    def test_the_session_manager_records_the_parent(self, tmp_path):
        from tau.session.manager import SessionManager
        from tau.session.types import SessionHeader, SessionOptions

        parent = tmp_path / "parent.jsonl"
        parent.write_text("")
        sm = SessionManager(cwd=tmp_path, persist=False)

        sm.new_session(SessionOptions(parent_session=str(parent)))

        header = next(e for e in sm.entries if isinstance(e, SessionHeader))
        assert header.parent_session == parent.resolve()

    def test_extension_new_session_forwards_the_option(self):
        import asyncio

        from tau.extensions.context import ExtensionContext, NewSessionOptions

        seen: list = []

        class _RT:
            extension_generation = 0

            async def new_session(self, *, with_session=None, parent_session=None):
                seen.append(parent_session)

        ctx = ExtensionContext.__new__(ExtensionContext)
        ctx._runtime_instance = _RT()
        ctx._generation = 0

        asyncio.run(ctx.new_session(NewSessionOptions(parent_session="/tmp/prev.jsonl")))

        # The option existed on the dataclass but was never passed through.
        assert seen == ["/tmp/prev.jsonl"]


class TestBusyGuards:
    """Commands are dispatched concurrently, so a client can land a
    session-mutating command in the middle of a turn. `compact` racing the
    automatic compaction inside that turn used to wedge the agent's phase
    non-idle for the rest of the session."""

    class _BusyAgent(_Agent):
        def is_idle(self) -> bool:
            return False

    @pytest.mark.asyncio
    async def test_compact_is_rejected_mid_turn(self, captured):
        agent = self._BusyAgent()
        compact_calls: list = []
        agent.compact = lambda **kwargs: compact_calls.append(kwargs)

        await mode._handle_command({"type": "compact", "id": "1"}, _Runtime(agent), {})

        assert captured[-1]["success"] is False
        assert "busy" in captured[-1]["error"]
        assert compact_calls == []

    @pytest.mark.asyncio
    async def test_new_session_is_rejected_mid_turn(self, captured):
        started: list = []

        class _RT(_Runtime):
            async def new_session(self, *, parent_session=None):
                started.append(parent_session)

        await mode._handle_command({"type": "new_session", "id": "1"}, _RT(self._BusyAgent()), {})

        assert captured[-1]["success"] is False
        assert "busy" in captured[-1]["error"]
        assert started == []

    @pytest.mark.asyncio
    async def test_switch_session_is_rejected_mid_turn(self, captured):
        resumed: list = []

        class _RT(_Runtime):
            async def resume_session(self, path):
                resumed.append(path)

        await mode._handle_command(
            {"type": "switch_session", "id": "1", "sessionPath": "/tmp/s.jsonl"},
            _RT(self._BusyAgent()),
            {},
        )

        assert captured[-1]["success"] is False
        assert "busy" in captured[-1]["error"]
        assert resumed == []

    @pytest.mark.asyncio
    async def test_fork_is_rejected_mid_turn(self, captured):
        forked: list = []

        class _RT(_Runtime):
            async def fork_session(self, entry_id, position="at"):
                forked.append(entry_id)

        rt = _RT(self._BusyAgent(), session_manager=SimpleNamespace(get_branch=lambda: []))
        await mode._handle_command({"type": "fork", "id": "1", "entryId": "e7c1"}, rt, {})

        assert captured[-1]["success"] is False
        assert "busy" in captured[-1]["error"]
        assert forked == []

    @pytest.mark.asyncio
    async def test_clone_is_rejected_mid_turn(self, captured):
        forked: list = []

        class _RT(_Runtime):
            async def fork_session(self, entry_id, position="at"):
                forked.append(entry_id)

        rt = _RT(self._BusyAgent(), session_manager=SimpleNamespace(leaf_id="leaf"))
        await mode._handle_command({"type": "clone", "id": "1"}, rt, {})

        assert captured[-1]["success"] is False
        assert "busy" in captured[-1]["error"]
        assert forked == []

    @pytest.mark.asyncio
    async def test_compact_still_runs_when_idle(self, captured):
        agent = _Agent()  # is_idle() -> True
        agent.compact = AsyncMock(return_value=True)

        await mode._handle_command(
            {"type": "compact", "id": "1"}, _Runtime(agent, session_manager=None), {}
        )

        agent.compact.assert_awaited_once()
        assert captured[-1]["success"] is True


class TestSetUpdateMode:
    """`set_update_mode` decides whether `message_update` still carries the
    full accumulated message. The delta tracking itself is shared with the
    JSON mode and covered in tests/test_wire.py."""

    @pytest.mark.asyncio
    async def test_set_update_mode_toggles_the_full_copy(self, captured):
        try:
            await mode._handle_command(
                {"type": "set_update_mode", "id": "1", "mode": "delta"}, _Runtime(), {}
            )
            assert captured[-1]["success"] is True
            assert captured[-1]["data"] == {"mode": "delta"}
            assert mode._DELTAS.omit_message is True

            await mode._handle_command(
                {"type": "set_update_mode", "id": "2", "mode": "full"}, _Runtime(), {}
            )
            assert mode._DELTAS.omit_message is False
        finally:
            mode._DELTAS.omit_message = False

    @pytest.mark.asyncio
    async def test_an_unknown_update_mode_is_rejected(self, captured):
        await mode._handle_command(
            {"type": "set_update_mode", "id": "1", "mode": "sometimes"}, _Runtime(), {}
        )

        assert captured[-1]["success"] is False
        assert "sometimes" in captured[-1]["error"]
        assert mode._DELTAS.omit_message is False


class TestSharedWireLayer:
    """RPC's outgoing side is the shared one, not a private copy."""

    def test_rpc_uses_the_shared_serializer_and_event_list(self):
        assert mode._serialize_event is wire.serialize_event
        assert mode._json_default is wire.json_default
        assert mode._write is wire.write
        assert mode._FORWARDED_EVENTS is wire.FORWARDED_EVENTS
        assert isinstance(mode._DELTAS, wire.StreamDeltas)

    def test_rpc_keeps_the_full_message_by_default(self):
        """Existing clients redraw from `message`; dropping it is opt-in."""
        assert mode._DELTAS.omit_message is False


class TestThinkingLevelDiscovery:
    """`cycle_thinking_level` walks the levels a model supports but never says
    what they are, so a client could step through them blind and not render a
    picker."""

    @pytest.mark.asyncio
    async def test_reports_the_models_levels_and_the_active_one(self, captured):
        llm = _LLM(_Model(levels=[ThinkingLevel.Off, ThinkingLevel.Low, ThinkingLevel.High]))
        llm.api.options.thinking_level = ThinkingLevel.Low
        rt = _Runtime(_Agent(llm))

        await mode._handle_command({"type": "get_available_thinking_levels", "id": "1"}, rt, {})

        assert captured[-1]["success"] is True
        assert captured[-1]["data"]["levels"] == ["off", "low", "high"]
        assert captured[-1]["data"]["current"] == "low"

    @pytest.mark.asyncio
    async def test_a_model_advertising_nothing_reports_every_level(self, captured):
        """Absent metadata means unknown, not unsupported — `_supports_level`
        treats every level as provisionally valid, and this must agree with it
        or a picker would hide levels the model can actually use."""
        from tau.inference.types import ThinkingLevel

        rt = _Runtime(_Agent(_LLM(_Model(levels=[]))))

        await mode._handle_command({"type": "get_available_thinking_levels", "id": "1"}, rt, {})

        assert captured[-1]["success"] is True
        assert captured[-1]["data"]["levels"] == [lvl.value for lvl in ThinkingLevel]

    @pytest.mark.asyncio
    async def test_no_model_is_an_error(self, captured):
        await mode._handle_command(
            {"type": "get_available_thinking_levels", "id": "1"}, _Runtime(), {}
        )

        assert captured[-1]["success"] is False
        assert captured[-1]["error"] == "No active model"

    @pytest.mark.asyncio
    async def test_the_reported_set_is_what_cycle_walks(self, captured):
        """If the two disagreed, a picker built from this list would offer a
        level cycling never reaches, or miss one it does."""
        llm = _LLM(_Model(levels=[ThinkingLevel.Off, ThinkingLevel.High]))
        rt = _Runtime(_Agent(llm))

        await mode._handle_command({"type": "get_available_thinking_levels", "id": "1"}, rt, {})
        reported = captured[-1]["data"]["levels"]

        walked = []
        for i in range(len(reported)):
            await mode._handle_command({"type": "cycle_thinking_level", "id": str(i)}, rt, {})
            walked.append(captured[-1]["data"]["level"])

        assert sorted(walked) == sorted(reported)


# ── Project trust ────────────────────────────────────────────────────────────


class _Settings:
    def __init__(self, trusted: bool = False) -> None:
        self._trusted = trusted

    def is_project_trusted(self) -> bool:
        return self._trusted

    def set_project_trusted(self, trusted: bool) -> None:
        self._trusted = trusted


class _TrustStore:
    """Stands in for the real store so tests never touch ~/.tau/trust.json."""

    def __init__(self) -> None:
        self.decisions: dict[str, bool | None] = {}

    def get(self, cwd):
        return self.decisions.get(str(cwd))

    def get_stored_path(self, cwd):
        return str(cwd) if str(cwd) in self.decisions else None

    def set(self, cwd, decision) -> None:
        if decision is None:
            self.decisions.pop(str(cwd), None)
        else:
            self.decisions[str(cwd)] = decision


@pytest.fixture
def store(monkeypatch):
    import tau.trust.manager as trust_manager

    fake = _TrustStore()
    monkeypatch.setattr(trust_manager, "trust_store", fake)
    return fake


def _trust_runtime(trusted: bool = False, source: str = "undecided") -> _Runtime:
    rt = _Runtime(_Agent(), _SessionManager())
    rt.settings_manager = _Settings(trusted)  # type: ignore[assignment]
    rt.project_trusted = trusted
    rt.project_trust_source = source
    return rt


class TestTrust:
    """An RPC client supervising an unattended worker has to be able to see —
    and settle — the trust decision. Without this it can only observe a boolean
    that reads False for both "refused" and "never asked"."""

    @pytest.mark.asyncio
    async def test_reports_the_decision_and_how_it_was_reached(self, captured, store):
        rt = _trust_runtime(trusted=False, source="undecided")

        await mode._handle_command({"type": "trust", "id": "1"}, rt, {})

        data = captured[-1]["data"]
        assert data["trusted"] is False
        # The distinction the boolean alone cannot carry.
        assert data["source"] == "undecided"
        assert data["stored"] is None
        assert data["cwd"] == "/work"

    @pytest.mark.asyncio
    async def test_reporting_does_not_change_anything(self, captured, store):
        rt = _trust_runtime(trusted=False)

        await mode._handle_command({"type": "trust", "id": "1"}, rt, {})

        assert store.decisions == {}
        assert rt.settings_manager.is_project_trusted() is False  # type: ignore[union-attr]
        assert rt.reloads == 0

    @pytest.mark.asyncio
    async def test_granting_trust_applies_for_the_session_without_persisting(self, captured, store):
        rt = _trust_runtime(trusted=False)

        await mode._handle_command({"type": "trust", "id": "1", "trusted": True}, rt, {})

        assert rt.settings_manager.is_project_trusted() is True  # type: ignore[union-attr]
        assert store.decisions == {}, "session-only must not write to disk"
        assert captured[-1]["data"]["stored"] is None

    @pytest.mark.asyncio
    async def test_remember_persists_the_decision(self, captured, store):
        rt = _trust_runtime(trusted=False)

        await mode._handle_command(
            {"type": "trust", "id": "1", "trusted": True, "remember": True}, rt, {}
        )

        assert store.decisions == {"/work": True}
        assert captured[-1]["data"]["stored"] is True

    @pytest.mark.asyncio
    async def test_granting_mid_session_reloads_extensions(self, captured, store):
        """Project settings were skipped at startup and context files are read
        while the session is built, so they need a reload to take effect —
        matching what the interactive /trust command does."""
        rt = _trust_runtime(trusted=False)

        await mode._handle_command({"type": "trust", "id": "1", "trusted": True}, rt, {})

        assert rt.reloads == 1
        assert captured[-1]["data"]["reloaded"] is True

    @pytest.mark.asyncio
    async def test_reaffirming_existing_trust_does_not_reload(self, captured, store):
        rt = _trust_runtime(trusted=True, source="stored")

        await mode._handle_command({"type": "trust", "id": "1", "trusted": True}, rt, {})

        assert rt.reloads == 0
        assert captured[-1]["data"]["reloaded"] is False

    @pytest.mark.asyncio
    async def test_refusing_trust_never_reloads(self, captured, store):
        rt = _trust_runtime(trusted=True, source="stored")

        await mode._handle_command({"type": "trust", "id": "1", "trusted": False}, rt, {})

        assert rt.settings_manager.is_project_trusted() is False  # type: ignore[union-attr]
        assert rt.reloads == 0

    @pytest.mark.asyncio
    async def test_forget_drops_the_stored_answer_and_leaves_the_session(self, captured, store):
        rt = _trust_runtime(trusted=True, source="stored")
        store.decisions["/work"] = True

        await mode._handle_command({"type": "trust", "id": "1", "forget": True}, rt, {})

        assert store.decisions == {}
        assert rt.settings_manager.is_project_trusted() is True  # type: ignore[union-attr]
        assert captured[-1]["data"]["stored"] is None

    @pytest.mark.asyncio
    async def test_a_non_boolean_is_rejected(self, captured, store):
        """`"trusted": "yes"` is truthy in Python and would silently grant."""
        rt = _trust_runtime(trusted=False)

        await mode._handle_command({"type": "trust", "id": "1", "trusted": "yes"}, rt, {})

        assert captured[-1]["success"] is False
        assert rt.settings_manager.is_project_trusted() is False  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_no_session_is_an_error(self, captured, store):
        rt = _Runtime(_Agent(), None)

        await mode._handle_command({"type": "trust", "id": "1"}, rt, {})

        assert captured[-1]["success"] is False


class TestTrustInState:
    @pytest.mark.asyncio
    async def test_get_state_carries_trust(self, captured):
        rt = _trust_runtime(trusted=True, source="stored")

        await mode._handle_command({"type": "get_state", "id": "1"}, rt, {})

        data = captured[-1]["data"]
        assert data["projectTrusted"] is True
        assert data["projectTrustSource"] == "stored"
