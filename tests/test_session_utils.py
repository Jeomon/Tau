"""Tests for tau/session/utils.py — session ID generation, path encoding, and file parsing."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from tau.message.types import (
    AssistantMessage,
    CompactionSummaryMessage,
    ImageContent,
    TerminalExecutionMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)
from tau.session.types import MessageEntry, SessionHeader
from tau.session.utils import (
    build_session_info,
    find_most_recent_session,
    generate_id,
    generate_timestamp,
    get_default_session_dir,
    get_last_activity_time,
    get_session_modified_date,
    is_message_with_contents,
    is_valid_session_file,
    list_sessions_from_dir,
    read_session_file,
    to_llm_messages,
)


class TestGenerateId:
    def test_returns_string(self):
        result = generate_id(set())
        assert isinstance(result, str)

    def test_avoids_existing_ids(self):
        existing = {"aaaabbbb"}
        # generate_id should not return any id already in the set
        results = {generate_id(existing) for _ in range(50)}
        assert "aaaabbbb" not in results

    def test_length_is_8(self):
        result = generate_id(set())
        assert len(result) == 8

    def test_uniqueness_across_calls(self):
        ids = {generate_id(set()) for _ in range(100)}
        # With uuid4, collisions are astronomically unlikely
        assert len(ids) > 90


class TestGetDefaultSessionDir:
    def test_creates_directory(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        cwd = "/home/user/project"
        result = get_default_session_dir(cwd, sessions_dir=sessions_dir)
        assert result.exists()
        assert result.is_dir()

    def test_encodes_path_safely(self, tmp_path):
        cwd = "/home/user/my-project"
        result = get_default_session_dir(cwd, sessions_dir=tmp_path)
        # Directory name should not contain raw slashes
        assert "/" not in result.name

    def test_idempotent(self, tmp_path):
        cwd = "/home/user/project"
        r1 = get_default_session_dir(cwd, sessions_dir=tmp_path)
        r2 = get_default_session_dir(cwd, sessions_dir=tmp_path)
        assert r1 == r2

    def test_different_cwds_produce_different_dirs(self, tmp_path):
        r1 = get_default_session_dir("/home/user/a", sessions_dir=tmp_path)
        r2 = get_default_session_dir("/home/user/b", sessions_dir=tmp_path)
        assert r1 != r2


class TestIsValidSessionFile:
    def _write_session(self, path: Path, header: dict) -> Path:
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        return path

    def test_valid_file(self, tmp_path):
        f = tmp_path / "session.jsonl"
        # SessionType.SESSION_HEADER == "session"
        header = {
            "type": "session",
            "id": "abc123",
            "cwd": "/tmp",
            "timestamp": time.time(),
            "parent_session": None,
        }
        self._write_session(f, header)
        assert is_valid_session_file(f) is True

    def test_empty_file_is_invalid(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert is_valid_session_file(f) is False

    def test_nonexistent_file(self, tmp_path):
        assert is_valid_session_file(tmp_path / "nope.jsonl") is False

    def test_invalid_json_header(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text("not json\n")
        assert is_valid_session_file(f) is False

    def test_wrong_type_header(self, tmp_path):
        f = tmp_path / "wrong.jsonl"
        f.write_text(json.dumps({"type": "message", "role": "user"}) + "\n")
        assert is_valid_session_file(f) is False


class TestFindMostRecentSession:
    def _make_valid_session(self, path: Path) -> Path:
        header = {
            "type": "session",
            "id": path.stem,
            "cwd": "/tmp",
            "timestamp": time.time(),
            "parent_session": None,
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        return path

    def test_returns_none_for_empty_dir(self, tmp_path):
        result = find_most_recent_session(tmp_path)
        assert result is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        result = find_most_recent_session(tmp_path / "nope")
        assert result is None

    def test_returns_most_recent_file(self, tmp_path):
        old = self._make_valid_session(tmp_path / "old.jsonl")
        newer = self._make_valid_session(tmp_path / "new.jsonl")
        # Set mtimes explicitly to avoid filesystem resolution issues
        os.utime(old, (1_000_000.0, 1_000_000.0))
        os.utime(newer, (1_000_001.0, 1_000_001.0))
        result = find_most_recent_session(tmp_path)
        assert result == newer

    def test_ignores_invalid_files(self, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("invalid\n")
        result = find_most_recent_session(tmp_path)
        assert result is None


class TestIsMessageWithContents:
    def test_user_with_text_content(self):
        msg = UserMessage.from_text("hello")
        assert is_message_with_contents(msg) is True

    def test_user_with_image_content(self):
        # Minimal PNG bytes
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        msg = UserMessage(contents=[ImageContent(images=[png])])
        assert is_message_with_contents(msg) is True

    def test_assistant_with_text_content(self):
        msg = AssistantMessage.from_text("hi")
        assert is_message_with_contents(msg) is True

    def test_assistant_with_only_tool_calls_returns_false(self):
        msg = AssistantMessage(contents=[ToolCallContent(id="1", name="fn", args={})])
        assert is_message_with_contents(msg) is False

    def test_compaction_summary_message_returns_false(self):
        msg = CompactionSummaryMessage(summary="summary here")
        assert is_message_with_contents(msg) is False

    def test_empty_user_message_returns_false(self):
        msg = UserMessage()
        assert is_message_with_contents(msg) is False


class TestCreateSessionId:
    def test_returns_string(self):
        from tau.session.utils import create_session_id

        assert isinstance(create_session_id(), str)

    def test_nonempty(self):
        from tau.session.utils import create_session_id

        assert len(create_session_id()) > 0

    def test_unique_across_calls(self):
        from tau.session.utils import create_session_id

        ids = {create_session_id() for _ in range(20)}
        assert len(ids) == 20


class TestGenerateTimestamp:
    def test_returns_float(self):
        ts = generate_timestamp()
        assert isinstance(ts, float)

    def test_close_to_now(self):
        before = time.time()
        ts = generate_timestamp()
        after = time.time()
        assert before <= ts <= after


class TestReadSessionFile:
    def _header_line(self, cwd: str = "/tmp") -> str:
        header = SessionHeader(cwd=Path(cwd))
        return header.model_dump_json()

    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = read_session_file(tmp_path / "nope.jsonl")
        assert result == []

    def test_valid_file_returns_entries(self, tmp_path):
        f = tmp_path / "session.jsonl"
        f.write_text(self._header_line() + "\n", encoding="utf-8")
        entries = read_session_file(f)
        assert len(entries) == 1
        assert isinstance(entries[0], SessionHeader)

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert read_session_file(f) == []

    def test_invalid_json_lines_are_skipped(self, tmp_path):
        f = tmp_path / "mixed.jsonl"
        f.write_text(self._header_line() + "\nnot-json\n", encoding="utf-8")
        entries = read_session_file(f)
        assert len(entries) == 1

    def test_wrong_header_type_returns_empty(self, tmp_path):
        f = tmp_path / "wrong.jsonl"
        user_entry = MessageEntry(message=UserMessage.from_text("hi"))
        f.write_text(user_entry.model_dump_json() + "\n", encoding="utf-8")
        assert read_session_file(f) == []


class TestGetLastActivityTime:
    def _entry(self, msg, ts: float) -> MessageEntry:
        msg.timestamp = ts
        entry = MessageEntry(message=msg)
        entry.timestamp = ts
        return entry

    def test_empty_entries_returns_none(self):
        assert get_last_activity_time([]) is None

    def test_returns_max_timestamp(self):
        e1 = self._entry(UserMessage.from_text("a"), 100.0)
        e2 = self._entry(UserMessage.from_text("b"), 200.0)
        result = get_last_activity_time([e1, e2])
        assert result == 200.0

    def test_skips_non_content_messages(self):
        tool_entry = self._entry(
            AssistantMessage(contents=[ToolCallContent(id="1", name="fn", args={})]),
            999.0,
        )
        user_entry = self._entry(UserMessage.from_text("hi"), 50.0)
        result = get_last_activity_time([tool_entry, user_entry])
        assert result == 50.0

    def test_compaction_message_skipped(self):
        c_entry = self._entry(CompactionSummaryMessage(summary="sum"), 500.0)
        result = get_last_activity_time([c_entry])
        assert result is None


class TestGetSessionModifiedDate:
    def _entry(self, msg, ts: float) -> MessageEntry:
        msg.timestamp = ts
        entry = MessageEntry(message=msg)
        entry.timestamp = ts
        return entry

    def test_uses_last_activity_time(self):
        entry = self._entry(UserMessage.from_text("hi"), 1_000_000.0)
        result = get_session_modified_date([entry])
        assert isinstance(result, datetime)
        assert result == datetime.fromtimestamp(1_000_000.0)

    def test_falls_back_to_header_timestamp(self):
        header = SessionHeader(cwd=Path("/tmp"))
        header.timestamp = 500_000.0
        result = get_session_modified_date([], header=header)
        assert result == datetime.fromtimestamp(500_000.0)

    def test_falls_back_to_now_with_no_header(self):
        before = datetime.now()
        result = get_session_modified_date([])
        after = datetime.now()
        assert before <= result <= after


class TestBuildSessionInfo:
    def _make_session_file(
        self, path: Path, cwd: str = "/tmp", extra_lines: list[str] | None = None
    ) -> Path:
        header = SessionHeader(cwd=Path(cwd))
        lines = [header.model_dump_json()]
        if extra_lines:
            lines.extend(extra_lines)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_returns_session_info(self, tmp_path):
        f = self._make_session_file(tmp_path / "s.jsonl")
        info = build_session_info(f)
        assert info is not None
        assert info.cwd == Path("/tmp")

    def test_returns_none_for_no_header(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        entry = MessageEntry(message=UserMessage.from_text("hi"))
        f.write_text(entry.model_dump_json() + "\n", encoding="utf-8")
        assert build_session_info(f) is None

    def test_counts_messages(self, tmp_path):
        entry1 = MessageEntry(message=UserMessage.from_text("q"))
        entry2 = MessageEntry(message=AssistantMessage.from_text("a"))
        f = self._make_session_file(
            tmp_path / "s.jsonl",
            extra_lines=[entry1.model_dump_json(), entry2.model_dump_json()],
        )
        info = build_session_info(f)
        assert info is not None
        assert info.message_count == 2

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert build_session_info(f) is None


class TestListSessionsFromDir:
    def _make_session(self, path: Path) -> Path:
        header = SessionHeader(cwd=Path("/tmp"))
        path.write_text(header.model_dump_json() + "\n", encoding="utf-8")
        return path

    def test_returns_empty_for_nonexistent_dir(self, tmp_path):
        result = list_sessions_from_dir(tmp_path / "no_such_dir")
        assert result == []

    def test_returns_session_infos(self, tmp_path):
        self._make_session(tmp_path / "a.jsonl")
        self._make_session(tmp_path / "b.jsonl")
        result = list_sessions_from_dir(tmp_path)
        assert len(result) == 2

    def test_skips_invalid_files(self, tmp_path):
        self._make_session(tmp_path / "good.jsonl")
        (tmp_path / "bad.jsonl").write_text("garbage\n", encoding="utf-8")
        result = list_sessions_from_dir(tmp_path)
        assert len(result) == 1

    def test_progress_callback_called(self, tmp_path):
        self._make_session(tmp_path / "s.jsonl")
        calls: list[tuple[int, int]] = []
        list_sessions_from_dir(tmp_path, on_progress=lambda n, t: calls.append((n, t)))
        assert len(calls) == 1

    def test_empty_dir_returns_empty(self, tmp_path):
        assert list_sessions_from_dir(tmp_path) == []


class TestToLlmMessages:
    def test_user_message_passes_through(self):
        msg = UserMessage.from_text("hello")
        result = to_llm_messages([msg])
        assert result == [msg]

    def test_assistant_with_text_passes_through(self):
        msg = AssistantMessage.from_text("world")
        result = to_llm_messages([msg])
        assert result == [msg]

    def test_empty_assistant_message_is_skipped(self):
        msg = AssistantMessage(contents=[])
        result = to_llm_messages([msg])
        assert result == []

    def test_compaction_summary_becomes_user_message(self):
        msg = CompactionSummaryMessage(summary="summarised context")
        result = to_llm_messages([msg])
        assert len(result) == 1
        assert isinstance(result[0], UserMessage)
        c = result[0].contents[0]
        assert isinstance(c, TextContent)
        assert "summarised context" in c.content
        assert "<context-summary>" in c.content

    def test_terminal_execution_becomes_user_message(self):
        msg = TerminalExecutionMessage(command="ls", output="file.txt")
        result = to_llm_messages([msg])
        assert len(result) == 1
        assert isinstance(result[0], UserMessage)

    def test_terminal_execution_excluded_is_skipped(self):
        msg = TerminalExecutionMessage(command="ls", output="file.txt", exclude=True)
        result = to_llm_messages([msg])
        assert result == []

    def test_tool_message_passes_through(self):
        msg = ToolMessage.from_result(ToolResultContent(id="1", content="ok"))
        result = to_llm_messages([msg])
        assert result == [msg]

    def test_custom_message_is_skipped(self):
        from tau.message.types import CustomMessage

        msg = CustomMessage(custom_type="special")
        result = to_llm_messages([msg])
        assert result == []

    def test_assistant_with_tool_call_passes_through(self):
        msg = AssistantMessage(contents=[ToolCallContent(id="x", name="fn", args={})])
        result = to_llm_messages([msg])
        assert result == [msg]

    def test_assistant_with_thinking_passes_through(self):
        msg = AssistantMessage(contents=[ThinkingContent(content="thoughts")])
        result = to_llm_messages([msg])
        assert result == [msg]

    def test_mixed_messages(self):
        user = UserMessage.from_text("q")
        empty_assistant = AssistantMessage(contents=[])
        compaction = CompactionSummaryMessage(summary="ctx")
        asst = AssistantMessage.from_text("a")
        result = to_llm_messages([user, empty_assistant, compaction, asst])
        assert len(result) == 3
        assert result[0] is user
        assert isinstance(result[1], UserMessage)
        assert result[2] is asst
