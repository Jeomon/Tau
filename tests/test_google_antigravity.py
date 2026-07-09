"""Tests for the Google Cloud Code Assist (antigravity) message conversion.

Covers three related fixes to _messages_to_contents:

1. functionResponse must correlate to its functionCall by tool *name* (matching
   pi's convention), not by the per-call id — using the id there breaks
   correlation whenever Gemini assigns a real, distinct id.
2. Claude models routed through this API require an explicit "id" on both
   functionCall and functionResponse parts, or multi-turn tool use fails with
   "tool_use.id: Field required".
3. Gemini 3 rejects a functionCall part with no thoughtSignature (e.g. history
   replayed from a Claude turn never had one) — falls back to a text part.
4. thoughtSignature must not be replayed across a model switch mid-session,
   since it may not be valid bytes for whichever backend is now active.
"""

from __future__ import annotations

from tau.inference.api.text.google_antigravity import _messages_to_contents
from tau.message.types import AssistantMessage, ToolCallContent, ToolMessage, ToolResultContent, UserMessage


def _history() -> list:
    return [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="read_file", args={"path": "x"})]),
        ToolMessage(
            contents=[ToolResultContent(id="tc1", content="file contents", tool_name="read_file")]
        ),
    ]


def test_function_response_name_is_tool_name_not_call_id() -> None:
    _, contents = _messages_to_contents(_history(), "gemini-2.5-pro")

    tool_turn = next(c for c in contents if any("functionResponse" in p for p in c["parts"]))
    response = next(p["functionResponse"] for p in tool_turn["parts"] if "functionResponse" in p)
    assert response["name"] == "read_file"
    assert response["response"] == {"output": "file contents"}


def test_function_response_error_uses_error_key() -> None:
    # Gemini 3 Flash Preview strictly requires "output"/"error" (not
    # "result"/"isError") — older Gemini models tolerated the wrong shape.
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="read_file", args={})]),
        ToolMessage(
            contents=[
                ToolResultContent(
                    id="tc1", content="not found", tool_name="read_file", is_error=True
                )
            ]
        ),
    ]

    _, contents = _messages_to_contents(messages, "gemini-2.5-pro")

    tool_turn = next(c for c in contents if any("functionResponse" in p for p in c["parts"]))
    response = next(p["functionResponse"] for p in tool_turn["parts"] if "functionResponse" in p)
    assert response["response"] == {"error": "not found"}


def test_claude_model_gets_explicit_tool_call_id() -> None:
    _, contents = _messages_to_contents(_history(), "claude-sonnet-4-6")

    call_turn = next(c for c in contents if any("functionCall" in p for p in c["parts"]))
    call = next(p["functionCall"] for p in call_turn["parts"] if "functionCall" in p)
    assert call["id"] == "tc1"

    response_turn = next(c for c in contents if any("functionResponse" in p for p in c["parts"]))
    response = next(p["functionResponse"] for p in response_turn["parts"] if "functionResponse" in p)
    assert response["id"] == "tc1"


def test_gemini_model_omits_tool_call_id() -> None:
    _, contents = _messages_to_contents(_history(), "gemini-2.5-pro")

    call_turn = next(c for c in contents if any("functionCall" in p for p in c["parts"]))
    call = next(p["functionCall"] for p in call_turn["parts"] if "functionCall" in p)
    assert "id" not in call


def test_gemini3_unsigned_tool_call_falls_back_to_text() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="bash", args={"command": "ls"})]),
    ]

    _, contents = _messages_to_contents(messages, "gemini-3-pro-preview")

    model_turn = contents[-1]
    assert not any("functionCall" in p for p in model_turn["parts"])
    assert any("bash" in p.get("text", "") for p in model_turn["parts"])


def test_gemini3_signed_tool_call_stays_a_function_call() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[
                ToolCallContent(
                    id="tc1", name="bash", args={"command": "ls"}, metadata={"thoughtSignature": "sig"}
                )
            ]
        ),
    ]

    _, contents = _messages_to_contents(messages, "gemini-3-pro-preview")

    model_turn = contents[-1]
    assert any("functionCall" in p for p in model_turn["parts"])


def test_distrust_thought_signatures_drops_stored_signature() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[
                ToolCallContent(
                    id="tc1", name="bash", args={"command": "ls"}, metadata={"thoughtSignature": "sig"}
                )
            ]
        ),
    ]

    _, contents = _messages_to_contents(
        messages, "gemini-2.5-pro", distrust_thought_signatures=True
    )

    model_turn = contents[-1]
    fc_part = next(p for p in model_turn["parts"] if "functionCall" in p)
    assert "thoughtSignature" not in fc_part

    # Same history on a gemini-3 model: distrust + no signature -> falls back to text.
    _, gemini3_contents = _messages_to_contents(
        messages, "gemini-3-pro-preview", distrust_thought_signatures=True
    )
    assert not any("functionCall" in p for p in gemini3_contents[-1]["parts"])
