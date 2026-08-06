"""Covers error visibility in InputHandler._invoke/_steer/_queue_followup.

Regression coverage for two bugs:

1. An exception caught during _invoke/_steer/_queue_followup used to be
   written to the spinner label and the spinner immediately stopped —
   Spinner.render() returns [] while inactive, so the message was set and
   then instantly made invisible; it never reached the user.
2. Fixing (1) naively (always posting a notify banner) would double-report
   a mid-stream provider error: the engine already persists and renders
   that case as a labeled AssistantMessage(stop_reason=Error) card via
   AgentHookHandler._on_message_end. _error_already_rendered detects that
   case (branch tip moved to a fresh error message) and skips the banner.

Constructs a bare InputHandler (bypassing __init__, which needs a live
Runtime/Layout/TUI) and only sets the attributes each method touches,
following the pattern in test_input_handler_paste.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tau.inference.types import StopReason
from tau.message.types import AssistantMessage
from tau.modes.interactive.input_handler import InputHandler


def make_handler() -> InputHandler:
    h = object.__new__(InputHandler)
    h._layout = MagicMock()
    h._tui = MagicMock()
    h._runtime = MagicMock()
    h._invoke_task = None
    return h


def _notify_calls(h: InputHandler) -> list[tuple]:
    return [c.args for c in h._notify.call_args_list]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_invoke_error_before_any_message_is_notified() -> None:
    """A failure with no corresponding session entry (e.g. client construction
    blows up before streaming starts) has no other way to reach the user."""
    h = make_handler()
    h._notify = MagicMock()
    h._runtime.session_manager.get_leaf_id.return_value = "leaf-1"
    h._runtime.user_input = AsyncMock(side_effect=ValueError("boom"))

    await h._invoke("hello")

    assert _notify_calls(h) == [("error: boom",)]
    assert h._layout.spinner.stop.called


@pytest.mark.asyncio
async def test_invoke_error_already_rendered_is_not_duplicated() -> None:
    """A mid-stream provider error ends the turn with a persisted
    AssistantMessage(stop_reason=Error) already rendered by
    AgentHookHandler._on_message_end — the generic notice must not repeat it."""
    h = make_handler()
    h._notify = MagicMock()
    h._runtime.session_manager.get_leaf_id.side_effect = ["leaf-1", "leaf-2"]
    h._runtime.session_manager.find_last_assistant_message.return_value = AssistantMessage(
        stop_reason=StopReason.Error, error="rate limited"
    )
    h._runtime.user_input = AsyncMock(side_effect=RuntimeError("Agent failed: rate limited."))

    await h._invoke("hello")

    assert _notify_calls(h) == []
    assert h._layout.spinner.stop.called


@pytest.mark.asyncio
async def test_invoke_error_with_stale_leaf_message_is_still_notified() -> None:
    """The branch tip moved, but the new tip isn't an error-stopped assistant
    message (e.g. some other entry was appended) — still needs the notice."""
    h = make_handler()
    h._notify = MagicMock()
    h._runtime.session_manager.get_leaf_id.side_effect = ["leaf-1", "leaf-2"]
    h._runtime.session_manager.find_last_assistant_message.return_value = AssistantMessage(
        stop_reason=StopReason.Stop
    )
    h._runtime.user_input = AsyncMock(side_effect=RuntimeError("boom"))

    await h._invoke("hello")

    assert _notify_calls(h) == [("error: boom",)]


@pytest.mark.asyncio
async def test_invoke_cancelled_error_is_swallowed_silently() -> None:
    import asyncio

    h = make_handler()
    h._notify = MagicMock()
    h._runtime.session_manager.get_leaf_id.return_value = "leaf-1"
    h._runtime.user_input = AsyncMock(side_effect=asyncio.CancelledError())

    await h._invoke("hello")

    assert _notify_calls(h) == []


@pytest.mark.asyncio
async def test_steer_error_is_notified() -> None:
    h = make_handler()
    h._notify = MagicMock()
    h._runtime.agent._engine.steer = AsyncMock(side_effect=ValueError("boom"))

    await h._steer("hello")

    assert _notify_calls(h) == [("error: boom",)]


@pytest.mark.asyncio
async def test_queue_followup_error_is_notified() -> None:
    h = make_handler()
    h._notify = MagicMock()
    h._runtime.agent.is_idle.return_value = False
    h._runtime.agent._engine.follow_up = AsyncMock(side_effect=ValueError("boom"))

    await h._queue_followup("hello")

    assert _notify_calls(h) == [("error: boom",)]
