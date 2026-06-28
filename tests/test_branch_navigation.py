"""Runtime-level branch summarization regression tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from tau.hooks.service import Hooks
from tau.inference.types import TextEndEvent
from tau.message.types import AssistantMessage, BranchSummaryMessage, TextContent, UserMessage
from tau.runtime.service import Runtime
from tau.session.manager import SessionManager
from tau.session.types import BranchSummaryEntry


class _LLM:
    model = SimpleNamespace(input_limit=4_000)

    async def invoke(self, context):
        return [TextEndEvent(text=TextContent(content="Abandoned work summary"))]


class _Settings:
    def get_branch_summary_reserve_tokens(self) -> int:
        return 500


def _runtime(tmp_path) -> tuple[Any, SessionManager, str, str]:
    manager = SessionManager(cwd=tmp_path, persist=False)
    root_id = manager.append_message(UserMessage.from_text("root"))
    old_leaf_id = manager.append_message(AssistantMessage.from_text("abandoned work"))

    runtime: Any = Runtime.__new__(Runtime)
    runtime._context = SimpleNamespace(
        session_manager=manager,
        settings_manager=_Settings(),
        llm=_LLM(),
        hooks=Hooks(),
    )
    runtime._layout = None
    runtime._emit_session_start = AsyncMock()
    return runtime, manager, root_id, old_leaf_id


def test_summary_is_attached_to_destination_branch(tmp_path) -> None:
    runtime, manager, target_id, old_leaf_id = _runtime(tmp_path)

    result = asyncio.run(runtime.navigate_tree(target_id, summarize=True))

    assert result is True
    leaf_id = manager.get_leaf_id()
    assert leaf_id is not None
    summary_entry = manager.get_entry(leaf_id)
    assert isinstance(summary_entry, BranchSummaryEntry)
    assert summary_entry.parent_id == target_id
    assert summary_entry.from_id == old_leaf_id
    context = manager.build_session_context()
    assert any(isinstance(message, BranchSummaryMessage) for message in context.messages)


def test_provider_failure_still_navigates_without_summary(tmp_path) -> None:
    runtime, manager, target_id, _ = _runtime(tmp_path)

    async def fail(context):
        raise RuntimeError("provider unavailable")

    runtime._context.llm.invoke = fail
    notifications: list[str] = []
    runtime.notify = lambda message: notifications.append(message)

    result = asyncio.run(runtime.navigate_tree(target_id, summarize=True))

    assert result is True
    assert manager.get_leaf_id() == target_id
    assert any("provider unavailable" in message for message in notifications)
