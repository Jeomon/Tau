"""Tests for tau/inference/api/text/utils.py — OpenAI/Anthropic format converters."""

from __future__ import annotations

import json

from tau.inference.api.text.utils import (
    anthropic_apply_message_cache,
    anthropic_cache_control,
    anthropic_output_config,
    openai_assistant_content,
    openai_prompt_cache_retention,
    openai_response_format,
    openai_user_content,
    resolve_cache_retention,
)
from tau.message.types import ImageContent, TextContent, ToolCallContent


class TestOpenaiUserContent:
    def test_single_text_returns_string(self):
        result = openai_user_content([TextContent(content="hello")])
        assert result == "hello"

    def test_multiple_texts_returns_list(self):
        result = openai_user_content([TextContent(content="a"), TextContent(content="b")])
        assert isinstance(result, list)
        assert result[0] == {"type": "text", "text": "a"}
        assert result[1] == {"type": "text", "text": "b"}

    def test_image_url_passthrough(self):
        img = ImageContent(images=["https://example.com/img.png"])
        result = openai_user_content([img])
        assert isinstance(result, list)
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "https://example.com/img.png"

    def test_image_bytes_as_data_uri(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        img = ImageContent(images=[png])
        result = openai_user_content([img])
        assert isinstance(result, list)
        url = result[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_mixed_text_and_image(self):
        items = [TextContent(content="describe:"), ImageContent(images=["https://x.com/a.jpg"])]
        result = openai_user_content(items)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"

    def test_empty_content_returns_list(self):
        result = openai_user_content([])
        assert result == []

    def test_dimension_note_appended(self):
        img = ImageContent(images=["https://x.com/a.jpg"], dimension_note="scale: 2x")
        result = openai_user_content([img])
        assert isinstance(result, list)
        last = result[-1]
        assert last == {"type": "text", "text": "scale: 2x"}


class TestOpenaiAssistantContent:
    def test_text_only(self):
        text, tools, thinking = openai_assistant_content([TextContent(content="hello")])
        assert text == "hello"
        assert tools == []
        assert thinking == ""

    def test_empty_returns_none_text(self):
        text, tools, thinking = openai_assistant_content([])
        assert text is None
        assert tools == []
        assert thinking == ""

    def test_tool_call(self):
        tc = ToolCallContent(id="call1", name="search", args={"q": "test"})
        text, tools, _thinking = openai_assistant_content([tc])
        assert text is None
        assert len(tools) == 1
        assert tools[0]["id"] == "call1"
        assert tools[0]["function"]["name"] == "search"
        assert json.loads(tools[0]["function"]["arguments"]) == {"q": "test"}

    def test_mixed_text_and_tool_calls(self):
        items = [
            TextContent(content="I'll search"),
            ToolCallContent(id="c1", name="fn", args={}),
        ]
        text, tools, _thinking = openai_assistant_content(items)
        assert text == "I'll search"
        assert len(tools) == 1

    def test_multiple_texts_concatenated(self):
        text, _tools, _thinking = openai_assistant_content(
            [TextContent(content="foo"), TextContent(content="bar")]
        )
        assert text == "foobar"


class TestOpenaiResponseFormat:
    def test_none_returns_none(self):
        assert openai_response_format(None) is None

    def test_structured_format_returned(self):
        from tau.inference.types import StructuredResponseFormat

        fmt = StructuredResponseFormat(name="output", schema={"type": "object"})
        result = openai_response_format(fmt)
        assert result is not None
        assert result["type"] == "json_schema"
        assert result["json_schema"]["name"] == "output"
        assert result["json_schema"]["schema"] == {"type": "object"}

    def test_dict_schema_passed_through(self):
        from tau.inference.types import StructuredResponseFormat

        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        fmt = StructuredResponseFormat(name="resp", schema=schema)
        result = openai_response_format(fmt)
        assert result["json_schema"]["schema"] == schema


class TestAnthropicOutputConfig:
    def test_none_returns_none(self):
        assert anthropic_output_config(None) is None

    def test_structured_format_returned(self):
        from tau.inference.types import StructuredResponseFormat

        fmt = StructuredResponseFormat(name="out", schema={"type": "string"})
        result = anthropic_output_config(fmt)
        assert result is not None
        assert result["format"]["type"] == "json_schema"
        assert result["format"]["schema"] == {"type": "string"}


class TestResolveCacheRetention:
    def test_defaults_to_short(self, monkeypatch):
        monkeypatch.delenv("TAU_CACHE_RETENTION", raising=False)
        assert resolve_cache_retention() == "short"

    def test_explicit_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("TAU_CACHE_RETENTION", "short")
        assert resolve_cache_retention("long") == "long"

    def test_env_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("TAU_CACHE_RETENTION", "long")
        assert resolve_cache_retention() == "long"

    def test_env_case_and_whitespace_normalised(self, monkeypatch):
        monkeypatch.setenv("TAU_CACHE_RETENTION", "  LONG ")
        assert resolve_cache_retention() == "long"

    def test_unrecognised_falls_back_to_short(self, monkeypatch):
        monkeypatch.delenv("TAU_CACHE_RETENTION", raising=False)
        assert resolve_cache_retention("forever") == "short"


class TestAnthropicCacheControl:
    def test_short_is_bare_ephemeral(self):
        assert anthropic_cache_control(True, "short") == {"type": "ephemeral"}

    def test_long_with_support_adds_1h_ttl(self):
        assert anthropic_cache_control(True, "long") == {"type": "ephemeral", "ttl": "1h"}

    def test_long_without_support_stays_5m(self):
        assert anthropic_cache_control(False, "long") == {"type": "ephemeral"}

    def test_none_disables_caching(self):
        assert anthropic_cache_control(True, "none") is None


class TestOpenaiPromptCacheRetention:
    def test_long_with_support_is_24h(self):
        assert openai_prompt_cache_retention(True, "long") == "24h"

    def test_long_without_support_is_none(self):
        assert openai_prompt_cache_retention(False, "long") is None

    def test_short_is_none(self):
        assert openai_prompt_cache_retention(True, "short") is None

    def test_none_retention_is_none(self):
        assert openai_prompt_cache_retention(True, "none") is None


class TestAnthropicApplyMessageCache:
    def _msgs(self):
        return [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        ]

    def test_default_marker_is_5m_ephemeral(self):
        out = anthropic_apply_message_cache(self._msgs())
        assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_custom_marker_threaded_through(self):
        marker = {"type": "ephemeral", "ttl": "1h"}
        out = anthropic_apply_message_cache(self._msgs(), marker=marker)
        assert out[-1]["content"][-1]["cache_control"] == marker
        # string content is wrapped into a text block carrying the marker
        assert out[-2]["content"][-1]["cache_control"] == marker

    def test_none_marker_injects_no_breakpoints(self):
        out = anthropic_apply_message_cache(self._msgs(), marker=None)
        assert "cache_control" not in out[-2]
        assert all("cache_control" not in b for b in out[-1]["content"])

    def test_skip_tail_excludes_ephemeral_messages(self):
        out = anthropic_apply_message_cache(self._msgs(), skip_tail=1)
        # last message is skipped; breakpoint lands on the earlier one
        assert all("cache_control" not in b for b in out[-1]["content"])
