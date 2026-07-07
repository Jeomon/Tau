"""Tests for tau/session/compaction.py — token estimation, compaction logic."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from tau.inference.types import StopReason, TextEndEvent
from tau.message.types import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ImageContent,
    TerminalExecutionMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    Usage,
    UserMessage,
)
from tau.session.compaction import (
    ESTIMATED_IMAGE_TOKENS,
    TOOL_RESULT_MAX_CHARS,
    CompactionSettings,
    effective_usage_tokens,
    estimate_context_tokens,
    estimate_tokens,
    generate_summary,
    serialize_conversation,
    should_compact,
    validated_compaction_settings,
)
from tau.session.compaction import _count_text_tokens as _count_tokens
from tau.session.compaction import _TOKEN_ESTIMATE_SAFETY_FACTOR as _SAFETY


class TestEstimateTokens:
    """estimate_tokens's job is correct per-message-type text extraction and
    combination (with image/tool-call handling) — the exact tokenizer count for
    a given string is the tokenizer's own behavior, not ours, so these use
    _count_text_tokens (the same function estimate_tokens itself calls) as the
    oracle rather than hardcoding numbers tied to a specific BPE vocabulary.
    """

    def test_user_text_message(self):
        msg = UserMessage.from_text("hello world")
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("hello world")

    def test_empty_user_message(self):
        msg = UserMessage()
        tokens = estimate_tokens(msg)
        assert tokens >= 1  # minimum of 1

    def test_assistant_text_message(self):
        msg = AssistantMessage.from_text("a" * 400)
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("a" * 400)

    def test_assistant_thinking_counted(self):
        msg = AssistantMessage(contents=[ThinkingContent(content="t" * 200)])
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("t" * 200)

    def test_assistant_tool_call_counted(self):
        args = {"path": "/a/b"}
        name = "read_file"
        msg = AssistantMessage(contents=[ToolCallContent(id="1", name=name, args=args)])
        expected = _count_tokens(name + json.dumps(args))
        assert estimate_tokens(msg) == expected

    def test_tool_message_result(self):
        result = ToolResultContent(id="1", content="r" * 800)
        msg = ToolMessage.from_result(result)
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("r" * 800)

    def test_terminal_execution_message(self):
        msg = TerminalExecutionMessage(command="ls", output="file1\nfile2")
        tokens = estimate_tokens(msg)
        expected = _count_tokens("ls" + "file1\nfile2")
        assert tokens == expected

    def test_compaction_summary_message(self):
        msg = CompactionSummaryMessage(summary="s" * 400)
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("s" * 400)

    def test_branch_summary_message(self):
        msg = BranchSummaryMessage(summary="s" * 200)
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("s" * 200)

    def test_custom_message_text_counted(self):
        msg = CustomMessage(custom_type="info", contents=[TextContent(content="c" * 100)])
        tokens = estimate_tokens(msg)
        assert tokens == _count_tokens("c" * 100)

    def test_image_uses_flat_token_estimate(self):
        msg = UserMessage(contents=[ImageContent(images=["fake-base64"])])
        assert estimate_tokens(msg) == ESTIMATED_IMAGE_TOKENS

    def test_image_and_text_combined(self):
        msg = UserMessage(
            contents=[TextContent(content="hello"), ImageContent(images=["fake-base64"])]
        )
        assert estimate_tokens(msg) == _count_tokens("hello") + ESTIMATED_IMAGE_TOKENS


class TestCountTextTokensFallback:
    """Covers the chars/4 heuristic path directly, since conftest.py's
    session-scoped fixture forces the real tokenizer to be loaded for the rest
    of the suite — this exercises the fallback formula without needing to
    actually make the tokenizer unavailable.
    """

    def test_fallback_formula_matches_safety_factor(self, monkeypatch):
        from tau.session import compaction as compaction_module

        monkeypatch.setattr(compaction_module, "_encoding", None)
        # Prevent _start_loading_encoding from clobbering the patched None back
        # to the real encoding mid-test (it's a no-op once _encoding_load_started
        # is True, which conftest.py's fixture already guarantees).
        text = "x" * 400
        assert compaction_module._count_text_tokens(text) == int(
            len(text) * _SAFETY
        ) // 4

    def test_empty_text_is_zero(self):
        assert _count_tokens("") == 0


class TestEstimateContextTokens:
    def test_no_messages(self):
        result = estimate_context_tokens([])
        assert result.tokens == 0

    def test_uses_heuristic_without_usage(self):
        msgs = [UserMessage.from_text("hello")]
        result = estimate_context_tokens(msgs)
        assert result.tokens >= 1
        assert result.last_usage_index is None

    def test_uses_assistant_usage_as_anchor(self):
        u = Usage(input_tokens=100, output_tokens=50)
        asst = AssistantMessage(contents=[TextContent(content="reply")])
        asst.usage = u
        asst.stop_reason = StopReason.Stop

        msgs = [UserMessage.from_text("q"), asst]
        result = estimate_context_tokens(msgs)
        assert result.usage_tokens == 150
        assert result.last_usage_index == 1

    def test_does_not_double_count_inclusive_cache_tokens(self):
        usage = Usage(
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=800,
            input_tokens_include_cache_read=True,
        )
        assert effective_usage_tokens(usage) == 1_100

    def test_adds_exclusive_cache_tokens(self):
        usage = Usage(
            input_tokens=200,
            output_tokens=100,
            cache_read_tokens=800,
            cache_write_tokens=50,
        )
        assert effective_usage_tokens(usage) == 1_150

    def test_heuristic_includes_request_overhead(self):
        message = UserMessage.from_text("hello")
        system_prompt = "You are a careful coding assistant. " * 20
        without_overhead = estimate_context_tokens([message])
        with_overhead = estimate_context_tokens([message], system_prompt=system_prompt)
        assert with_overhead.tokens >= without_overhead.tokens + _count_tokens(system_prompt)

    def test_ignore_usage_uses_heuristic_after_model_change(self):
        assistant = AssistantMessage.from_text("short")
        assistant.usage = Usage(input_tokens=100_000)
        result = estimate_context_tokens([assistant], ignore_usage=True)
        assert result.last_usage_index is None
        assert result.tokens < 100_000

    def test_skips_aborted_assistant(self):
        u = Usage(input_tokens=1000, output_tokens=0)
        aborted = AssistantMessage(contents=[])
        aborted.usage = u
        aborted.stop_reason = StopReason.Abort

        msgs = [UserMessage.from_text("q"), aborted]
        result = estimate_context_tokens(msgs)
        # Should fall back to heuristic since only aborted assistant exists
        assert result.last_usage_index is None


class TestShouldCompact:
    def test_disabled_settings_never_compact(self):
        settings = CompactionSettings(enabled=False, reserve_tokens=1000)
        assert should_compact(100_000, 200_000, settings) is False

    def test_zero_context_window_never_compact(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=1000)
        assert should_compact(100_000, 0, settings) is False

    def test_compacts_when_over_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=10_000)
        # 95_000 tokens in a 100_000 window → needs 10k reserve → over threshold
        assert should_compact(95_000, 100_000, settings) is True

    def test_no_compact_when_within_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=10_000)
        # 50_000 tokens in a 100_000 window → plenty of room
        assert should_compact(50_000, 100_000, settings) is False

    def test_exactly_at_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=10_000)
        assert should_compact(90_000, 100_000, settings) is True

    def test_one_over_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=10_000)
        assert should_compact(90_001, 100_000, settings) is True

    def test_clamps_budgets_to_small_context_window(self):
        settings = validated_compaction_settings(
            CompactionSettings(reserve_tokens=8_000, keep_recent_tokens=20_000),
            context_window=10_000,
        )
        assert settings.reserve_tokens < 10_000
        assert settings.keep_recent_tokens + settings.reserve_tokens < 10_000


class TestSummaryBudget:
    def test_summary_prompt_is_bounded_by_model_input_limit(self):
        class FakeLLM:
            model = SimpleNamespace(input_limit=1_000)

            def __init__(self) -> None:
                self.prompt = ""

            async def invoke(self, context):
                self.prompt = context.messages[0].contents[0].content
                return [TextEndEvent(text=TextContent(content="summary"))]

        llm = FakeLLM()
        result = asyncio.run(
            generate_summary(
                [UserMessage.from_text("x" * 4_000)],
                llm,  # type: ignore[arg-type]
                reserve_tokens=100,
            )
        )

        assert result == "summary"
        assert len(llm.prompt) <= (1_000 - 100) * 4 + 1
        assert "middle content omitted" in llm.prompt


class TestSerializeConversation:
    def test_user_message(self):
        msgs = [UserMessage.from_text("hello")]
        text = serialize_conversation(msgs)
        assert "[User]: hello" in text

    def test_assistant_message(self):
        msgs = [AssistantMessage.from_text("world")]
        text = serialize_conversation(msgs)
        assert "[Assistant]: world" in text

    def test_assistant_thinking(self):
        msg = AssistantMessage(contents=[ThinkingContent(content="my thought")])
        text = serialize_conversation([msg])
        assert "[Assistant thinking]: my thought" in text

    def test_assistant_tool_call(self):
        msg = AssistantMessage(
            contents=[ToolCallContent(id="1", name="read_file", args={"path": "/tmp/f"})]
        )
        text = serialize_conversation([msg])
        assert "[Assistant tool calls]: read_file" in text

    def test_tool_result(self):
        result = ToolResultContent(id="1", content="result text")
        msg = ToolMessage.from_result(result)
        text = serialize_conversation([msg])
        assert "[Tool result]: result text" in text

    def test_tool_result_truncated(self):
        long_content = "x" * (TOOL_RESULT_MAX_CHARS + 500)
        result = ToolResultContent(id="1", content=long_content)
        msg = ToolMessage.from_result(result)
        text = serialize_conversation([msg])
        assert "truncated" in text

    def test_terminal_execution_message(self):
        msg = TerminalExecutionMessage(command="ls -la", output="file.txt")
        text = serialize_conversation([msg])
        assert "[Terminal]: Ran `ls -la`" in text
        assert "file.txt" in text

    def test_compaction_summary(self):
        msg = CompactionSummaryMessage(summary="prior history summary")
        text = serialize_conversation([msg])
        assert "[Context Summary]:" in text
        assert "prior history summary" in text

    def test_branch_summary(self):
        msg = BranchSummaryMessage(summary="branch abandoned", from_id="abc")
        text = serialize_conversation([msg])
        assert "[Branch Summary]:" in text

    def test_messages_joined_with_double_newline(self):
        msgs = [UserMessage.from_text("q"), AssistantMessage.from_text("a")]
        text = serialize_conversation(msgs)
        assert "\n\n" in text

    def test_empty_messages(self):
        assert serialize_conversation([]) == ""

    def test_custom_message(self):
        msg = CustomMessage(custom_type="info", contents=[TextContent(content="custom text")])
        text = serialize_conversation([msg])
        assert "[info]: custom text" in text


class TestTruncate:
    def test_short_text_unchanged(self):
        from tau.session.compaction import _truncate

        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        from tau.session.compaction import _truncate

        assert _truncate("hello", 5) == "hello"

    def test_long_text_truncated(self):
        from tau.session.compaction import _truncate

        result = _truncate("a" * 100, 10)
        assert result.startswith("a" * 10)
        assert "truncated" in result

    def test_truncation_message_includes_count(self):
        from tau.session.compaction import _truncate

        result = _truncate("x" * 20, 5)
        assert "15" in result


class TestIsValidCutPoint:
    def test_user_message_entry_is_valid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import MessageEntry

        entry = MessageEntry(message=UserMessage.from_text("hi"))
        assert _is_valid_cut_point(entry) is True

    def test_terminal_message_entry_is_valid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import MessageEntry

        entry = MessageEntry(message=TerminalExecutionMessage(command="ls"))
        assert _is_valid_cut_point(entry) is True

    def test_assistant_with_content_is_valid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import MessageEntry

        entry = MessageEntry(message=AssistantMessage.from_text("reply"))
        assert _is_valid_cut_point(entry) is True

    def test_aborted_empty_assistant_is_invalid(self):
        from tau.inference.types import StopReason
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import MessageEntry

        msg = AssistantMessage(contents=[], stop_reason=StopReason.Abort)
        entry = MessageEntry(message=msg)
        assert _is_valid_cut_point(entry) is False

    def test_custom_message_entry_is_valid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import CustomMessageEntry

        entry = CustomMessageEntry(custom_type="info", content=[])
        assert _is_valid_cut_point(entry) is True

    def test_branch_summary_entry_is_valid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import BranchSummaryEntry

        entry = BranchSummaryEntry(from_id="abc", summary="sum")
        assert _is_valid_cut_point(entry) is True

    def test_tool_message_entry_is_invalid(self):
        from tau.session.compaction import _is_valid_cut_point
        from tau.session.types import MessageEntry

        entry = MessageEntry(
            message=ToolMessage.from_result(ToolResultContent(id="1", content="ok"))
        )
        assert _is_valid_cut_point(entry) is False


class TestLatestCompactionTimestamp:
    def test_empty_branch_returns_none(self):
        from tau.session.compaction import latest_compaction_timestamp

        assert latest_compaction_timestamp([]) is None

    def test_no_compaction_returns_none(self):
        from tau.session.compaction import latest_compaction_timestamp
        from tau.session.types import MessageEntry

        entries = [MessageEntry(message=UserMessage.from_text("hi"))]
        assert latest_compaction_timestamp(entries) is None

    def test_returns_most_recent_compaction_timestamp(self):
        from tau.session.compaction import latest_compaction_timestamp
        from tau.session.types import CompactionEntry

        c1 = CompactionEntry(summary="s1", first_kept_entry_id="x", tokens_before=100)
        c1.timestamp = 1000.0
        c2 = CompactionEntry(summary="s2", first_kept_entry_id="y", tokens_before=200)
        c2.timestamp = 2000.0
        assert latest_compaction_timestamp([c1, c2]) == 2000.0


class TestIsSilentOverflow:
    def test_zero_context_window_returns_false(self):
        from tau.session.compaction import is_silent_overflow

        msg = AssistantMessage.from_text("ok")
        assert is_silent_overflow(msg, 0) is False

    def test_normal_stop_within_window(self):
        from tau.session.compaction import is_silent_overflow

        msg = AssistantMessage.from_text("ok")
        msg.usage.input_tokens = 100
        assert is_silent_overflow(msg, 200_000) is False

    def test_stop_with_input_exceeding_window(self):
        from tau.inference.types import StopReason
        from tau.session.compaction import is_silent_overflow

        msg = AssistantMessage.from_text("ok")
        msg.stop_reason = StopReason.Stop
        msg.usage.input_tokens = 200_001
        assert is_silent_overflow(msg, 200_000) is True

    def test_length_stop_zero_output_near_window(self):
        from tau.inference.types import StopReason
        from tau.session.compaction import is_silent_overflow

        msg = AssistantMessage(contents=[])
        msg.stop_reason = StopReason.Length
        msg.usage.input_tokens = 199_000
        msg.usage.output_tokens = 0
        assert is_silent_overflow(msg, 200_000) is True

    def test_length_stop_with_output_tokens(self):
        from tau.inference.types import StopReason
        from tau.session.compaction import is_silent_overflow

        msg = AssistantMessage.from_text("partial")
        msg.stop_reason = StopReason.Length
        msg.usage.input_tokens = 199_000
        msg.usage.output_tokens = 100
        assert is_silent_overflow(msg, 200_000) is False


class TestFindCutPoint:
    def _user_entry(self, text: str = "q"):
        from tau.session.types import MessageEntry

        return MessageEntry(message=UserMessage.from_text(text))

    def _asst_entry(self, text: str = "a"):
        from tau.session.types import MessageEntry

        return MessageEntry(message=AssistantMessage.from_text(text))

    def test_empty_entries_returns_start(self):
        from tau.session.compaction import find_cut_point

        result = find_cut_point([], 0, 0, 1000)
        assert result.first_kept_entry_index == 0

    def test_small_conversation_no_split(self):
        from tau.session.compaction import find_cut_point

        entries = [self._user_entry(), self._asst_entry()]
        result = find_cut_point(entries, 0, len(entries), keep_recent_tokens=10_000)
        assert result.first_kept_entry_index == 0
        assert result.is_split_turn is False

    def test_keeps_recent_messages(self):
        from tau.session.compaction import find_cut_point

        # Build a long sequence; keep_recent_tokens is tiny so only the last few survive
        entries = [self._user_entry(f"msg{i}") for i in range(10)]
        result = find_cut_point(entries, 0, len(entries), keep_recent_tokens=2)
        # Cut point should be well into the list
        assert result.first_kept_entry_index > 0

    def test_split_turn_detected(self):
        from tau.session.compaction import find_cut_point
        from tau.session.types import MessageEntry

        # user + many assistants (long) + user + asst at the end
        # keep_recent tiny so cut falls in the middle of a turn
        big_text = "word " * 1000
        entries = [
            self._user_entry("first"),
            MessageEntry(message=AssistantMessage.from_text(big_text)),
            self._user_entry("second"),
            self._asst_entry("short"),
        ]
        result = find_cut_point(entries, 0, len(entries), keep_recent_tokens=10)
        assert result.first_kept_entry_index >= 0


class TestPrepareCompaction:
    def _user_entry(self, text: str = "q"):
        from tau.session.types import MessageEntry

        return MessageEntry(message=UserMessage.from_text(text))

    def _asst_entry(self, text: str = "a"):
        from tau.session.types import MessageEntry

        return MessageEntry(message=AssistantMessage.from_text(text))

    def test_empty_entries_returns_none(self):
        from tau.session.compaction import prepare_compaction

        assert prepare_compaction([], CompactionSettings()) is None

    def test_last_entry_is_compaction_returns_none(self):
        from tau.session.compaction import prepare_compaction
        from tau.session.types import CompactionEntry

        entries = [
            self._user_entry(),
            CompactionEntry(summary="prev", first_kept_entry_id="x", tokens_before=100),
        ]
        assert prepare_compaction(entries, CompactionSettings()) is None

    def test_small_history_within_budget_returns_none(self):
        from tau.session.compaction import prepare_compaction

        entries = [self._user_entry(), self._asst_entry()]
        settings = CompactionSettings(keep_recent_tokens=100_000)
        assert prepare_compaction(entries, settings) is None

    def test_long_history_produces_preparation(self):
        from tau.session.compaction import prepare_compaction

        big_text = "word " * 2000
        # Old turn (to be summarised) followed by a new short turn to keep.
        # "new question" and "short answer" are ~2 tokens each with the real
        # tokenizer, so keep_recent_tokens=3 stops the backward walk right at
        # the "new question" user boundary — a clean cut, no split turn —
        # without needing to reach into the ~2000-token old turn.
        entries = [
            self._user_entry(big_text),
            self._asst_entry(big_text),
            self._user_entry("new question"),
            self._asst_entry("short answer"),
        ]
        settings = CompactionSettings(keep_recent_tokens=3)
        prep = prepare_compaction(entries, settings)
        assert prep is not None
        assert prep.first_kept_entry_id != ""
        assert len(prep.messages_to_summarize) > 0
