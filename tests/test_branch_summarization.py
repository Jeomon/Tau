"""Tests for tau/session/branch_summarization.py — pure helper functions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tau.inference.types import TextEndEvent
from tau.message.types import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
)
from tau.session.branch_summarization import (
    FileOperations,
    _compute_file_lists,
    _format_file_operations,
    generate_branch_summary,
    prepare_branch_entries,
)
from tau.session.types import MessageEntry


class TestComputeFileLists:
    def test_empty_ops(self):
        ops = FileOperations()
        read_only, modified = _compute_file_lists(ops)
        assert read_only == []
        assert modified == []

    def test_read_only_files(self):
        ops = FileOperations(read={"/a.py", "/b.py"})
        read_only, modified = _compute_file_lists(ops)
        assert sorted(read_only) == ["/a.py", "/b.py"]
        assert modified == []

    def test_written_files_are_modified(self):
        ops = FileOperations(written={"/out.py"})
        read_only, modified = _compute_file_lists(ops)
        assert "/out.py" in modified
        assert "/out.py" not in read_only

    def test_edited_files_are_modified(self):
        ops = FileOperations(edited={"/edit.py"})
        read_only, modified = _compute_file_lists(ops)
        assert "/edit.py" in modified

    def test_read_and_then_edited_is_not_read_only(self):
        ops = FileOperations(read={"/rw.py"}, edited={"/rw.py"})
        read_only, modified = _compute_file_lists(ops)
        assert "/rw.py" not in read_only
        assert "/rw.py" in modified

    def test_both_edited_and_written(self):
        ops = FileOperations(edited={"/a.py"}, written={"/b.py"})
        read_only, modified = _compute_file_lists(ops)
        assert sorted(modified) == ["/a.py", "/b.py"]

    def test_output_is_sorted(self):
        ops = FileOperations(read={"/z.py", "/a.py", "/m.py"})
        read_only, _ = _compute_file_lists(ops)
        assert read_only == sorted(read_only)


class TestFormatFileOperations:
    def test_empty_lists_returns_empty(self):
        assert _format_file_operations([], []) == ""

    def test_read_files_only(self):
        result = _format_file_operations(["/a.py", "/b.py"], [])
        assert "<read-files>" in result
        assert "/a.py" in result
        assert "<modified-files>" not in result

    def test_modified_files_only(self):
        result = _format_file_operations([], ["/out.py"])
        assert "<modified-files>" in result
        assert "/out.py" in result
        assert "<read-files>" not in result

    def test_both_sections(self):
        result = _format_file_operations(["/r.py"], ["/w.py"])
        assert "<read-files>" in result
        assert "<modified-files>" in result
        assert "/r.py" in result
        assert "/w.py" in result

    def test_leading_newlines(self):
        result = _format_file_operations(["/r.py"], [])
        assert result.startswith("\n\n")


class TestPrepareBranchEntries:
    def _make_tool_entries(
        self,
        tool_name: str,
        path: str,
        *,
        is_error: bool = False,
    ) -> list[MessageEntry]:
        tc = ToolCallContent(id="tc1", name=tool_name, args={"path": path})
        result = ToolResultContent(
            id="tc1",
            content="failed" if is_error else "ok",
            is_error=is_error,
            tool_name=tool_name,
        )
        return [
            MessageEntry(message=AssistantMessage(contents=[tc])),
            MessageEntry(message=ToolMessage.from_result(result)),
        ]

    def _make_msg_entry(self, tool_name: str, path: str) -> MessageEntry:
        return self._make_tool_entries(tool_name, path)[0]

    def test_empty_entries(self):
        prep = prepare_branch_entries([])
        assert prep.messages == []
        assert prep.total_tokens == 0

    def test_collects_file_ops_from_read_tool(self):
        prep = prepare_branch_entries(self._make_tool_entries("read", "/data.py"))
        assert "/data.py" in prep.file_ops.read

    def test_collects_file_ops_from_write_tool(self):
        prep = prepare_branch_entries(self._make_tool_entries("write", "/out.py"))
        assert "/out.py" in prep.file_ops.written

    def test_collects_file_ops_from_edit_tool(self):
        prep = prepare_branch_entries(self._make_tool_entries("edit", "/src.py"))
        assert "/src.py" in prep.file_ops.edited

    def test_failed_tool_call_is_not_recorded(self):
        prep = prepare_branch_entries(self._make_tool_entries("edit", "/failed.py", is_error=True))
        assert "/failed.py" not in prep.file_ops.edited

    def test_tool_results_are_included_in_summary_messages(self):
        prep = prepare_branch_entries(self._make_tool_entries("read", "/data.py"))
        assert any(isinstance(message, ToolMessage) for message in prep.messages)

    def test_file_ops_are_collected_outside_message_budget(self):
        old_entries = self._make_tool_entries("write", "/old.py")
        recent_entries = [
            MessageEntry(message=AssistantMessage.from_text("x" * 1_000)),
        ]
        prep = prepare_branch_entries(old_entries + recent_entries, token_budget=1)
        assert "/old.py" in prep.file_ops.written

    def test_messages_collected(self):
        entry = self._make_msg_entry("read", "/x.py")
        prep = prepare_branch_entries([entry])
        assert len(prep.messages) == 1

    def test_token_budget_limits_messages(self):
        entries = [self._make_msg_entry("read", f"/file{i}.py") for i in range(10)]
        # A tiny token budget — only a few messages should be included
        prep_unlimited = prepare_branch_entries(entries)
        prep_limited = prepare_branch_entries(entries, token_budget=5)
        assert len(prep_limited.messages) <= len(prep_unlimited.messages)

    def test_multiple_entries_total_tokens(self):
        entries = [self._make_msg_entry("read", f"/f{i}.py") for i in range(3)]
        prep = prepare_branch_entries(entries)
        assert prep.total_tokens > 0


class TestGenerateBranchSummary:
    def test_uses_model_aware_bounded_prompt(self):
        class FakeLLM:
            model = SimpleNamespace(input_limit=1_000)

            def __init__(self) -> None:
                self.prompt = ""

            async def invoke(self, context):
                self.prompt = context.messages[0].contents[0].content
                return [TextEndEvent(text=TextContent(content="summary"))]

        llm = FakeLLM()
        entries = [
            MessageEntry(message=AssistantMessage.from_text("x" * 10_000)),
        ]

        result = asyncio.run(
            generate_branch_summary(
                entries,
                llm,  # type: ignore[arg-type]
                context_window=1_000,
                reserve_tokens=100,
            )
        )

        assert result.error is None
        assert result.summary is not None
        assert llm.prompt
        assert len(llm.prompt) <= (1_000 - 100) * 4
        assert "middle content omitted" in llm.prompt

    def test_provider_failure_returns_error(self):
        class FailingLLM:
            model = SimpleNamespace(input_limit=1_000)

            async def invoke(self, context):
                raise RuntimeError("provider unavailable")

        entries = [MessageEntry(message=AssistantMessage.from_text("work"))]
        result = asyncio.run(
            generate_branch_summary(
                entries,
                FailingLLM(),  # type: ignore[arg-type]
                context_window=1_000,
                reserve_tokens=100,
            )
        )

        assert result.summary is None
        assert result.error == "provider unavailable"
