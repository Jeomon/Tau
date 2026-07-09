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

    question_object = schema["properties"]["questions"]["items"]
    option_object = question_object["properties"]["options"]["items"]["anyOf"][1]
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
    assert response.response == {"output": "sunny"}


def test_tool_error_result_uses_error_key() -> None:
    # Gemini 3 Flash Preview strictly requires "output"/"error" (not
    # "result"/"isError") — older Gemini models tolerated the wrong shape.
    messages = [
        ToolMessage(
            contents=[
                ToolResultContent(
                    id="call-123",
                    tool_name="weather",
                    content="request failed",
                    is_error=True,
                )
            ]
        )
    ]

    _, contents = _messages_to_gemini(messages)

    response = contents[0].parts[0].function_response
    assert response is not None
    assert response.response == {"error": "request failed"}


def test_distrust_thought_signatures_forces_text_fallback() -> None:
    # Dropping the stored signature leaves the call unsigned, and an unsigned
    # functionCall part is rejected — so distrust forces the same text
    # fallback as never having had a signature at all.
    messages = [
        AssistantMessage(
            contents=[
                ToolCallContent(
                    id="call-123",
                    name="weather",
                    args={"city": "Pune"},
                    metadata={"thought_signature": base64.b64encode(b"gemini-sig").decode()},
                )
            ]
        )
    ]

    _, contents = _messages_to_gemini(messages, distrust_thought_signatures=True)

    part = contents[0].parts[0]
    assert part.function_call is None
    assert part.text is not None and "weather" in part.text
    assert part.thought_signature is None

    # Same history, not distrusted -> signature is replayed and it stays a
    # real functionCall as before.
    _, trusted_contents = _messages_to_gemini(messages, distrust_thought_signatures=False)
    trusted_part = trusted_contents[0].parts[0]
    assert trusted_part.function_call is not None
    assert trusted_part.thought_signature == b"gemini-sig"


def test_unsigned_tool_call_falls_back_to_text() -> None:
    # No thought_signature at all in metadata (e.g. history replayed from a
    # different provider, like Mistral, that never produces one) — Gemini
    # rejects an unsigned functionCall part outright.
    messages = [
        AssistantMessage(
            contents=[ToolCallContent(id="call-123", name="weather", args={"city": "Pune"})]
        )
    ]

    _, contents = _messages_to_gemini(messages)

    part = contents[0].parts[0]
    assert part.function_call is None
    assert part.text is not None and "weather" in part.text


def test_client_forwards_custom_base_url():
    from tau.inference.api.text.gemini_generate import GeminiGenerateAPI
    from tau.inference.types import LLMOptions

    api = GeminiGenerateAPI(LLMOptions(api_key="fake", base_url="https://custom.example.com/v1"))
    assert api._client._api_client._http_options.base_url == "https://custom.example.com/v1"


def test_gemini3_uses_thinking_level_not_budget():
    from tau.inference.api.text.gemini_generate import GeminiGenerateAPI
    from tau.inference.types import LLMOptions, ThinkingLevel

    api = GeminiGenerateAPI(LLMOptions(api_key="fake", thinking_level=ThinkingLevel.High))

    gemini3_config = api._build_config("gemini-3-pro-preview")
    assert gemini3_config.thinking_config.thinking_level == genai_types.ThinkingLevel.HIGH
    assert gemini3_config.thinking_config.thinking_budget is None

    other_config = api._build_config("gemini-2.5-pro")
    assert other_config.thinking_config.thinking_level is None
    assert other_config.thinking_config.thinking_budget is not None


def test_distrust_thought_signatures_never_leaks_into_extra_params():
    # distrust_thought_signatures is a dedicated LLMOptions field, not part of
    # extra_params — several providers (e.g. openai_completions.py) spread
    # extra_params directly into the outgoing wire request, so anything put
    # there is sent to the actual API. A real 400 ("Unsupported parameter(s):
    # distrust_thought_signatures") happened when this was set via
    # extra_params and a later /model switch landed on an OpenAI-compatible
    # provider (NVIDIA) that rejects unknown params.
    from tau.inference.types import LLMOptions

    options = LLMOptions(distrust_thought_signatures=True)
    assert options.extra_params is None
