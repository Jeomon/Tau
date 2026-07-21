"""Regression guard for pi #6832 — orphaned tool result must not reach the provider.

If a compaction (or extension-supplied) boundary keeps a tool RESULT whose
originating tool CALL was folded into the summary, the OpenAI Responses/Codex
API 400s permanently with "No tool call found for function call output". The
adapters defensively drop such orphaned function_call_output items.
"""

from __future__ import annotations

from tau.inference.api.text.openai_codex_responses import _messages_to_input as codex_to_input
from tau.inference.api.text.openai_responses import _messages_to_input as responses_to_input
from tau.inference.api.text.utils import drop_orphan_function_call_outputs
from tau.message.types import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)


class TestDropOrphanHelper:
    def test_drops_output_without_matching_call(self):
        items = [
            {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
            {"type": "function_call_output", "call_id": "ORPHAN", "output": "stale"},
        ]
        kept = drop_orphan_function_call_outputs(items)
        assert {i.get("call_id") for i in kept if i.get("type") == "function_call_output"} == {"c1"}
        # the paired call+output survive
        assert len(kept) == 2

    def test_keeps_calls_even_if_output_missing(self):
        # dangling calls are intentionally NOT dropped (reasoning-item ordering)
        items = [{"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"}]
        assert drop_orphan_function_call_outputs(items) == items

    def test_noop_when_all_paired(self):
        items = [
            {"type": "function_call", "call_id": "c1", "name": "x", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "y"},
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ]
        assert drop_orphan_function_call_outputs(items) == items


def _orphaned_history() -> list:
    """A reconstructed context where the tool CALL was folded away (compaction),
    leaving only its result — the pi #6832 shape."""
    return [
        UserMessage.from_text("summary stand-in for folded turns"),
        ToolMessage(contents=[ToolResultContent(id="call_folded", content="orphan result")]),
        UserMessage.from_text("continue"),
    ]


def _paired_history() -> list:
    return [
        AssistantMessage(
            contents=[
                TextContent(content="calling"),
                ToolCallContent(id="call_ok", name="read", args={"path": "x"}),
            ]
        ),
        ToolMessage(contents=[ToolResultContent(id="call_ok", content="file body")]),
    ]


def test_responses_adapter_drops_orphan_output():
    _, items = responses_to_input(_orphaned_history())
    assert not any(i.get("type") == "function_call_output" for i in items)


def test_codex_adapter_drops_orphan_output():
    _, items = codex_to_input(_orphaned_history())
    assert not any(i.get("type") == "function_call_output" for i in items)


def test_responses_adapter_keeps_paired_output():
    _, items = responses_to_input(_paired_history())
    calls = {i["call_id"] for i in items if i.get("type") == "function_call"}
    outs = {i["call_id"] for i in items if i.get("type") == "function_call_output"}
    assert calls == {"call_ok"} and outs == {"call_ok"}
