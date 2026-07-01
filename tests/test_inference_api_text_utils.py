"""Tests for tau/inference/api/text/utils.py — OpenAI/Anthropic format converters."""

from __future__ import annotations

import json

from tau.inference.api.text.utils import (
    anthropic_output_config,
    openai_assistant_content,
    openai_response_format,
    openai_user_content,
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
