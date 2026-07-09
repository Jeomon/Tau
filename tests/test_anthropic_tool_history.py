"""Anthropic rejects a request outright if tool_use/tool_result blocks exist
anywhere in history but the top-level `tools` param is absent — an empty list
must be sent explicitly (e.g. after an extension calls set_active_tools([])
mid-conversation, leaving prior tool calls in history but no tools this turn).
"""

from __future__ import annotations

from tau.inference.api.text.anthropic_claude_code import AnthropicClaudeCodeAPI
from tau.inference.api.text.anthropic_messages import AnthropicMessagesAPI
from tau.inference.api.text.anthropic_vertex import AnthropicVertexAPI
from tau.inference.model.types import Model
from tau.inference.types import LLMOptions

_MODEL = Model(id="claude-sonnet-5", name="Claude Sonnet 5", provider="anthropic")

_HISTORY_WITH_TOOL_USE = [
    {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "tc1", "name": "read", "input": {}}],
    },
    {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "ok"}],
    },
]

_HISTORY_WITHOUT_TOOL_USE = [
    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
]


def _api(cls):
    api = cls.__new__(cls)
    api.options = LLMOptions()
    return api


def test_empty_tools_key_included_when_history_has_tool_use():
    api = _api(AnthropicMessagesAPI)
    params = api._build_params(_MODEL, None, _HISTORY_WITH_TOOL_USE, tools=None)
    assert params.get("tools") == []


def test_tools_key_omitted_when_no_tool_history():
    api = _api(AnthropicMessagesAPI)
    params = api._build_params(_MODEL, None, _HISTORY_WITHOUT_TOOL_USE, tools=None)
    assert "tools" not in params


def test_actual_tools_still_win_over_empty_history_check():
    from unittest.mock import MagicMock

    tool = MagicMock()
    tool.name = "read"
    tool.description = "read a file"
    tool.schema.model_json_schema.return_value = {"type": "object", "properties": {}}

    api = _api(AnthropicMessagesAPI)
    params = api._build_params(_MODEL, None, _HISTORY_WITH_TOOL_USE, tools=[tool])
    assert len(params["tools"]) == 1
    assert params["tools"][0]["name"] == "read"


def test_claude_code_variant_also_includes_empty_tools_on_history():
    api = _api(AnthropicClaudeCodeAPI)
    api._current_api_key = None
    params = api._build_params(_MODEL, None, _HISTORY_WITH_TOOL_USE, tools=None)
    assert params.get("tools") == []


def test_vertex_variant_also_includes_empty_tools_on_history():
    api = _api(AnthropicVertexAPI)
    params = api._build_params(_MODEL, None, _HISTORY_WITH_TOOL_USE, tools=None)
    assert params.get("tools") == []
