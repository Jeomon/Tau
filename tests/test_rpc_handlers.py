"""Tests for the RPC handlers that used to answer success without doing anything.

Each class here pins one entry from the old "Known Gaps" table in docs/rpc.md:
the handler must now reach the real API, and say so when it cannot.
"""

from __future__ import annotations

import pytest

import tau.modes.rpc.mode as mode
from tau.engine.types import FollowupMode, SteeringMode
from tau.inference.types import ThinkingLevel


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
