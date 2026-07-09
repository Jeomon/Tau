"""Tests for the Google Cloud Code Assist (antigravity) message conversion.

Covers four related fixes to _messages_to_contents:

1. functionResponse must correlate to its functionCall by tool *name* (matching
   pi's convention), not by the per-call id — using the id there breaks
   correlation whenever Gemini assigns a real, distinct id.
2. Claude models routed through this API require an explicit "id" on both
   functionCall and functionResponse parts, or multi-turn tool use fails with
   "tool_use.id: Field required".
3. A functionCall part with no thoughtSignature is rejected — not just by
   Gemini 3, gemini-2.5-flash enforces it too — so history replayed from a
   turn with no signature (a different provider, or a model switch) falls
   back to a text part instead, for every model behind this API.
4. thoughtSignature must not be replayed across a model switch mid-session,
   since it may not be valid bytes for whichever backend is now active.
5. An empty ThinkingContent block (no text) must be dropped, not replayed as
   a thought part with empty text — Claude's backend behind this API rejects
   that with "thinking.thinking: Field required".
"""

from __future__ import annotations

from tau.inference.api.text.google_antigravity import _messages_to_contents
from tau.message.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)

_SIGNATURE = {"thoughtSignature": "sig"}


def _history() -> list:
    return [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[
                ToolCallContent(id="tc1", name="read_file", args={"path": "x"}, metadata=_SIGNATURE)
            ]
        ),
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
        AssistantMessage(
            contents=[ToolCallContent(id="tc1", name="read_file", args={}, metadata=_SIGNATURE)]
        ),
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


def test_unsigned_tool_call_downgrades_matching_result_too() -> None:
    # If the functionCall has no thoughtSignature it's downgraded to plain
    # text (see module docstring, point 3). Sending the paired
    # ToolResultContent as a functionResponse in that case leaves a
    # tool_result with no matching tool_use, which Claude rejects with
    # "unexpected `tool_use_id` found in `tool_result` blocks". The result
    # must be downgraded to text alongside its call.
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="read_file", args={"path": "x"})]),
        ToolMessage(
            contents=[ToolResultContent(id="tc1", content="file contents", tool_name="read_file")]
        ),
    ]

    _, contents = _messages_to_contents(messages, "claude-sonnet-4-6")

    for content in contents:
        for part in content["parts"]:
            assert "functionCall" not in part
            assert "functionResponse" not in part


def test_empty_thinking_content_is_dropped() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[ThinkingContent(content=""), TextContent(content="hello there")]
        ),
    ]

    _, contents = _messages_to_contents(messages, "claude-sonnet-4-6")

    model_turn = next(c for c in contents if c["role"] == "model")
    assert not any(p.get("thought") for p in model_turn["parts"])
    assert {"text": "hello there"} in model_turn["parts"]


def test_unsigned_tool_call_falls_back_to_text_on_gemini3() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="bash", args={"command": "ls"})]),
    ]

    _, contents = _messages_to_contents(messages, "gemini-3-pro-preview")

    model_turn = contents[-1]
    assert not any("functionCall" in p for p in model_turn["parts"])
    assert any("bash" in p.get("text", "") for p in model_turn["parts"])


def test_unsigned_tool_call_falls_back_to_text_on_gemini_2_5_too() -> None:
    # gemini-2.5-flash enforces the same requirement as Gemini 3 — confirmed by
    # a real 400 ("Function call is missing a thought_signature") after
    # switching from a provider (e.g. Mistral) that never produces one.
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(contents=[ToolCallContent(id="tc1", name="bash", args={"command": "ls"})]),
    ]

    _, contents = _messages_to_contents(messages, "gemini-2.5-flash")

    model_turn = contents[-1]
    assert not any("functionCall" in p for p in model_turn["parts"])
    assert any("bash" in p.get("text", "") for p in model_turn["parts"])


def test_signed_tool_call_stays_a_function_call() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[ToolCallContent(id="tc1", name="bash", args={"command": "ls"}, metadata=_SIGNATURE)]
        ),
    ]

    for model_id in ("gemini-3-pro-preview", "gemini-2.5-flash"):
        _, contents = _messages_to_contents(messages, model_id)
        model_turn = contents[-1]
        assert any("functionCall" in p for p in model_turn["parts"])


def test_distrust_thought_signatures_forces_text_fallback() -> None:
    messages = [
        UserMessage.from_text("hi"),
        AssistantMessage(
            contents=[ToolCallContent(id="tc1", name="bash", args={"command": "ls"}, metadata=_SIGNATURE)]
        ),
    ]

    for model_id in ("gemini-3-pro-preview", "gemini-2.5-flash"):
        _, contents = _messages_to_contents(
            messages, model_id, distrust_thought_signatures=True
        )
        model_turn = contents[-1]
        # Distrusting the stored signature leaves the call unsigned, so it now
        # falls back to text regardless of model — same path as never having
        # had a signature at all.
        assert not any("functionCall" in p for p in model_turn["parts"])
