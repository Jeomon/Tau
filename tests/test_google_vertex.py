"""Tests for the Google Vertex AI Gemini message conversion.

google_vertex.py was an older, never-updated copy of the Gemini message
conversion logic and was missing every fix applied to gemini_generate.py /
google_antigravity.py:

1. functionResponse must use "output"/"error" keys (Gemini 3 Flash Preview
   rejects the older "result"/"isError" shape) and correlate to its
   functionCall by tool *name*, not the per-call id.
2. thoughtSignature must be captured from the response and replayed on the
   next request — this file never captured it at all, so any thinking-enabled
   model routed through Vertex (gemini-3.1-pro, gemini-2.5-pro, etc., all
   thinking=True in the registry) would 400 on every second tool-calling turn.
3. A functionCall part with no thoughtSignature is rejected — falls back to a
   plain text description.
4. thoughtSignature must not be replayed across a model switch mid-session.
"""

from __future__ import annotations

import base64

from tau.inference.api.text.google_vertex import _messages_to_gemini
from tau.message.types import (
    AssistantMessage,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)

_SIG_B64 = base64.b64encode(b"vertex-sig").decode()


def _history() -> list:
    return [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[
                ToolCallContent(
                    id="tc1",
                    name="read_file",
                    args={"path": "x"},
                    metadata={"thought_signature": _SIG_B64},
                )
            ]
        ),
        ToolMessage(
            contents=[ToolResultContent(id="tc1", content="file contents", tool_name="read_file")]
        ),
    ]


def test_function_response_uses_output_key_and_tool_name() -> None:
    _, contents = _messages_to_gemini(_history())

    tool_part = contents[-1].parts[0]
    assert tool_part.function_response.name == "read_file"
    assert tool_part.function_response.response == {"output": "file contents"}


def test_function_response_error_uses_error_key() -> None:
    messages = [
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="read_file", args={})]),
        ToolMessage(
            contents=[
                ToolResultContent(
                    id="tc1", content="not found", tool_name="read_file", is_error=True
                )
            ]
        ),
    ]

    _, contents = _messages_to_gemini(messages)

    tool_part = contents[-1].parts[0]
    assert tool_part.function_response.response == {"error": "not found"}


def test_signed_tool_call_replays_signature() -> None:
    _, contents = _messages_to_gemini(_history())

    call_part = contents[1].parts[0]
    assert call_part.function_call is not None
    assert call_part.thought_signature == b"vertex-sig"


def test_unsigned_tool_call_falls_back_to_text() -> None:
    messages = [
        AssistantMessage(contents=[ToolCallContent(id="tc2", name="bash", args={"cmd": "ls"})])
    ]

    _, contents = _messages_to_gemini(messages)

    part = contents[0].parts[0]
    assert part.function_call is None
    assert part.text is not None and "bash" in part.text


def test_distrust_thought_signatures_forces_text_fallback() -> None:
    _, contents = _messages_to_gemini(_history(), distrust_thought_signatures=True)

    call_part = contents[1].parts[0]
    assert call_part.function_call is None
    assert "read_file" in call_part.text


def test_thinking_content_replays_signature() -> None:
    messages = [
        AssistantMessage(contents=[ThinkingContent(content="reasoning...", signature=_SIG_B64)])
    ]

    _, contents = _messages_to_gemini(messages)

    part = contents[0].parts[0]
    assert part.thought is True
    assert part.thought_signature == b"vertex-sig"


def test_distrust_thought_signatures_drops_thinking_signature() -> None:
    messages = [
        AssistantMessage(contents=[ThinkingContent(content="reasoning...", signature=_SIG_B64)])
    ]

    _, contents = _messages_to_gemini(messages, distrust_thought_signatures=True)

    part = contents[0].parts[0]
    assert part.thought_signature is None
