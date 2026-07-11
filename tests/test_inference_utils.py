"""Tests for tau/inference/api/text/utils.py — message format conversion utilities."""

from __future__ import annotations

import json

from tau.inference.api.text.utils import (
    anthropic_apply_message_cache,
    anthropic_messages_to_list,
    openai_messages_to_chat,
    parse_tool_args,
)
from tau.message.types import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)


class TestParseToolArgs:
    def test_dict_passthrough(self):
        d = {"key": "value"}
        assert parse_tool_args(d) == d

    def test_json_string(self):
        assert parse_tool_args('{"a": 1}') == {"a": 1}

    def test_empty_string_returns_empty_dict(self):
        assert parse_tool_args("") == {}

    def test_none_returns_empty_dict(self):
        assert parse_tool_args(None) == {}

    def test_invalid_json_returns_empty_dict(self):
        assert parse_tool_args("{not valid json}") == {}

    def test_json_array_returns_empty_dict(self):
        # We only accept dicts at top level
        assert parse_tool_args("[1, 2, 3]") == {}

    def test_nested_dict(self):
        s = '{"outer": {"inner": true}}'
        assert parse_tool_args(s) == {"outer": {"inner": True}}


class TestOpenaiMessagesToChat:
    def test_system_message(self):
        msg = SystemMessage.text("You are a helpful assistant.")
        result = openai_messages_to_chat([msg])
        assert result == [{"role": "system", "content": "You are a helpful assistant."}]

    def test_user_text_message(self):
        msg = UserMessage.from_text("Hello!")
        result = openai_messages_to_chat([msg])
        assert result == [{"role": "user", "content": "Hello!"}]

    def test_assistant_text_message(self):
        msg = AssistantMessage.from_text("Hi there!")
        result = openai_messages_to_chat([msg])
        assert result == [{"role": "assistant", "content": "Hi there!"}]

    def test_assistant_with_tool_calls(self):
        msg = AssistantMessage(
            contents=[
                ToolCallContent(id="call_1", name="my_tool", args={"x": 1}),
            ]
        )
        result = openai_messages_to_chat([msg])
        assert len(result) == 1
        entry = result[0]
        assert entry["role"] == "assistant"
        assert len(entry["tool_calls"]) == 1
        tc = entry["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "my_tool"
        assert json.loads(tc["function"]["arguments"]) == {"x": 1}

    def test_tool_message(self):
        result_content = ToolResultContent(id="call_1", content="result text")
        msg = ToolMessage.from_result(result_content)
        result = openai_messages_to_chat([msg])
        assert result == [{"role": "tool", "tool_call_id": "call_1", "content": "result text"}]

    def test_empty_user_message_skipped(self):
        msg = UserMessage()
        result = openai_messages_to_chat([msg])
        assert result == []

    def test_full_conversation(self):
        msgs = [
            SystemMessage.text("system"),
            UserMessage.from_text("user input"),
            AssistantMessage.from_text("assistant reply"),
        ]
        result = openai_messages_to_chat(msgs)
        assert [r["role"] for r in result] == ["system", "user", "assistant"]


class TestAnthropicMessagesToList:
    def test_system_message_extracted(self):
        msg = SystemMessage.text("Be helpful.")
        system, result = anthropic_messages_to_list([msg])
        assert system == "Be helpful."
        assert result == []

    def test_user_text_message(self):
        msg = UserMessage.from_text("Hello")
        _, result = anthropic_messages_to_list([msg])
        assert result == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]

    def test_assistant_text_message(self):
        msg = AssistantMessage.from_text("Hi")
        _, result = anthropic_messages_to_list([msg])
        assert result == [{"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}]

    def test_thinking_content_included_when_supported(self):
        msg = AssistantMessage(
            contents=[
                ThinkingContent(content="my thought", signature="sig123"),
            ]
        )
        _, result = anthropic_messages_to_list([msg], supports_thinking=True)
        entry = result[0]["content"][0]
        assert entry["type"] == "thinking"
        assert entry["thinking"] == "my thought"
        assert entry["signature"] == "sig123"

    def test_empty_thinking_dropped_without_signature(self):
        msg = AssistantMessage(
            contents=[
                ThinkingContent(content="", signature=""),
                TextContent(content="reply"),
            ]
        )
        _, result = anthropic_messages_to_list([msg], supports_thinking=True)
        content = result[0]["content"]
        assert [c["type"] for c in content] == ["text"]

    def test_empty_thinking_preserved_with_signature(self):
        msg = AssistantMessage(
            contents=[
                ThinkingContent(content="", signature="sig123"),
                TextContent(content="reply"),
            ]
        )
        _, result = anthropic_messages_to_list([msg], supports_thinking=True)
        content = result[0]["content"]
        assert content[0] == {"type": "thinking", "thinking": "", "signature": "sig123"}

    def test_thinking_content_merged_when_not_supported(self):
        msg = AssistantMessage(
            contents=[
                ThinkingContent(content="thought", signature="sig"),
                TextContent(content="reply"),
            ]
        )
        _, result = anthropic_messages_to_list([msg], supports_thinking=False)
        content = result[0]["content"]
        # Should be merged into a single text block
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert "thought" in content[0]["text"]
        assert "reply" in content[0]["text"]

    def test_tool_result_message(self):
        result_content = ToolResultContent(id="c1", content="ok", is_error=False)
        msg = ToolMessage.from_result(result_content)
        _, result = anthropic_messages_to_list([msg])
        assert result[0]["role"] == "user"
        tr = result[0]["content"][0]
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "c1"
        assert tr["content"] == "ok"

    def test_empty_user_message_skipped(self):
        msg = UserMessage()
        _, result = anthropic_messages_to_list([msg])
        assert result == []


class TestAnthropicApplyMessageCache:
    def _make_msgs(self, n: int) -> list[dict]:
        return [{"role": "user", "content": f"msg{i}"} for i in range(n)]

    def test_adds_cache_control_to_last_two(self):
        msgs = self._make_msgs(5)
        result = anthropic_apply_message_cache(msgs, n=2)
        # Last 2 should have cache_control injected
        last = result[-1]["content"]
        second_last = result[-2]["content"]
        assert isinstance(last, list) and last[-1].get("cache_control") == {"type": "ephemeral"}
        assert isinstance(second_last, list) and second_last[-1].get("cache_control") == {
            "type": "ephemeral"
        }

    def test_does_not_mutate_original(self):
        msgs = self._make_msgs(3)
        original_content = msgs[-1]["content"]
        anthropic_apply_message_cache(msgs, n=2)
        assert msgs[-1]["content"] == original_content

    def test_skip_tail(self):
        msgs = self._make_msgs(4)
        result = anthropic_apply_message_cache(msgs, n=2, skip_tail=1)
        # Last message should NOT have cache_control
        last = result[-1]
        assert "cache_control" not in last
        assert last["content"] == "msg3"

    def test_fewer_messages_than_n(self):
        msgs = self._make_msgs(1)
        # Should not error; just marks what's available
        result = anthropic_apply_message_cache(msgs, n=2)
        assert len(result) == 1

    def test_empty_list(self):
        result = anthropic_apply_message_cache([], n=2)
        assert result == []
