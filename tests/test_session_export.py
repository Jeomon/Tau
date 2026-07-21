"""Tests for tau/session/export.py — standalone HTML transcript export."""

from __future__ import annotations

from pathlib import Path

from tau.message.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultContent,
    UserMessage,
)
from tau.session.export import export_session_html, session_to_html
from tau.session.types import MessageEntry


class _SessionManager:
    """The slice of SessionManager the exporter touches."""

    def __init__(self, messages, name=None):
        self.session_id = "sess-123"
        self.cwd = Path("/work/project")
        self._entries = [
            MessageEntry(id=f"e{i}", parent_id=None, message=m) for i, m in enumerate(messages)
        ]
        self._name = name

    def get_branch(self):
        return self._entries

    def get_session_name(self):
        return self._name


def _html(messages, **kwargs):
    return session_to_html(_SessionManager(messages, **kwargs))


class TestDocument:
    def test_is_standalone(self):
        out = _html([UserMessage.from_text("hi")])
        assert out.startswith("<!doctype html>")
        assert "<style>" in out
        # No external references — the file must open offline.
        assert "http://" not in out and "https://" not in out
        assert "<script" not in out

    def test_header_carries_session_metadata(self):
        out = _html([UserMessage.from_text("hi")], name="My session")
        assert "My session" in out
        assert "sess-123" in out
        assert "/work/project" in out

    def test_empty_session_renders(self):
        out = _html([])
        assert "no messages" in out


class TestEscaping:
    def test_message_text_is_escaped(self):
        out = _html([UserMessage.from_text("<script>alert('x')</script> & more")])
        assert "<script>alert" not in out
        assert "&lt;script&gt;alert" in out
        assert "&amp; more" in out

    def test_session_name_is_escaped(self):
        out = _html([UserMessage.from_text("hi")], name="<b>bold</b>")
        assert "<b>bold</b>" not in out
        assert "&lt;b&gt;bold&lt;/b&gt;" in out


class TestContent:
    def test_user_and_assistant_text(self):
        out = _html(
            [
                UserMessage.from_text("what is 2+2"),
                AssistantMessage(contents=[TextContent(content="4")]),
            ]
        )
        assert "what is 2+2" in out
        assert "User" in out and "Assistant" in out

    def test_tool_call_and_result(self):
        out = _html(
            [
                AssistantMessage(
                    contents=[ToolCallContent(id="c1", name="read", args={"path": "a.py"})]
                ),
                UserMessage(
                    contents=[
                        ToolResultContent(id="c1", tool_name="read", content="file contents")
                    ]
                ),
            ]
        )
        assert "call read" in out
        assert "a.py" in out
        assert "result read" in out
        assert "file contents" in out

    def test_tool_error_is_marked(self):
        out = _html(
            [
                UserMessage(
                    contents=[
                        ToolResultContent(id="c1", tool_name="read", content="boom", is_error=True)
                    ]
                )
            ]
        )
        assert 'class="error"' in out

    def test_thinking_is_included_but_distinguished(self):
        out = _html([AssistantMessage(contents=[ThinkingContent(content="hmm")])])
        assert "hmm" in out
        assert 'class="thinking"' in out

    def test_media_is_noted_not_inlined(self):
        out = _html([UserMessage(contents=[ImageContent(images=["QUJD"])])])
        assert "attachment omitted" in out
        assert "QUJD" not in out  # base64 payload stays out of the file


class TestWriting:
    def test_writes_the_file_and_returns_the_path(self, tmp_path):
        target = tmp_path / "nested" / "transcript.html"
        result = export_session_html(_SessionManager([UserMessage.from_text("hi")]), target)

        assert result == target
        assert target.read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_creates_missing_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.html"
        export_session_html(_SessionManager([]), target)
        assert target.exists()


class TestRoleLabels:
    def test_terminal_messages_get_a_readable_label(self):
        from tau.message.types import TerminalExecutionMessage

        out = _html([TerminalExecutionMessage(command="ls -la", output="a.py", exit_code=0)])
        assert ">Terminal<" in out
        assert "terminal_execution" not in out
        assert "$ ls -la" in out
        assert "a.py" in out

    def test_nonzero_exit_and_cancellation_are_shown(self):
        from tau.message.types import TerminalExecutionMessage

        assert "exit 2" in _html([TerminalExecutionMessage(command="x", exit_code=2)])
        assert "cancelled" in _html(
            [TerminalExecutionMessage(command="x", exit_code=-9, cancelled=True)]
        )
