"""Tests for the Google AI Studio Gemini API adapter."""

import base64

from google.genai import types as genai_types

from tau.builtins.extensions.ask_user.schema import AskUserParams
from tau.inference.api.text.gemini_generate import _messages_to_gemini
from tau.inference.api.text.utils import gemini_tool_schema
from tau.inference.model.registry import ModelRegistry
from tau.message.types import (
    AssistantMessage,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
)


def test_gemini_tool_schema_removes_unsupported_examples() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "title": "Path",
                "examples": ["/tmp/file.txt"],
            },
            "offset": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "examples": [0, 100],
            },
        },
    }

    assert gemini_tool_schema(schema) == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
        },
    }


def test_gemini_tool_schema_preserves_property_named_title() -> None:
    schema = gemini_tool_schema(AskUserParams.model_json_schema())

    option_object = schema["properties"]["options"]["items"]["anyOf"][1]
    assert "title" in option_object["properties"]
    assert option_object["required"] == ["title"]


def test_google_ai_studio_models_are_registered() -> None:
    registry = ModelRegistry.from_text_builtins()

    assert registry.get("gemini-3.5-flash", provider="google") is not None
    assert registry.get("gemini-3.1-flash-lite", provider="google") is not None
    assert registry.get("gemini-3.1-pro-preview", provider="google") is not None
    assert registry.get("gemini-2.5-pro", provider="google") is not None
    assert registry.get("gemini-2.5-flash", provider="google") is not None
    assert registry.get("gemini-2.5-flash-lite", provider="google") is not None


def test_messages_preserve_function_call_id_and_signature() -> None:
    signature = b"gemini-thought-signature"
    messages = [
        AssistantMessage(
            contents=[
                ToolCallContent(
                    id="call-123",
                    name="weather",
                    args={"city": "Pune"},
                    metadata={"thought_signature": base64.b64encode(signature).decode("ascii")},
                )
            ]
        )
    ]

    _, contents = _messages_to_gemini(messages)

    part = contents[0].parts[0]
    assert part.function_call == genai_types.FunctionCall(
        id="call-123",
        name="weather",
        args={"city": "Pune"},
    )
    assert part.thought_signature == signature


def test_tool_result_uses_call_id_and_tool_name() -> None:
    messages = [
        ToolMessage(
            contents=[
                ToolResultContent(
                    id="call-123",
                    tool_name="weather",
                    content="sunny",
                )
            ]
        )
    ]

    _, contents = _messages_to_gemini(messages)

    response = contents[0].parts[0].function_response
    assert response is not None
    assert response.id == "call-123"
    assert response.name == "weather"
    assert response.response == {"result": "sunny"}
