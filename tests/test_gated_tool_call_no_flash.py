"""A gated tool call must never be painted before it is hidden.

`4746e77` hides a tool call until its `tool_call` gate resolves, so the
transcript does not read as though the call had already been made. The hiding
ran at `message_end`. Tool-call arguments stream in, and `_flush_pending`
paints the message on a ~60fps timer long before that — so a gated call
appeared, vanished when `message_end` claimed it, and came back on approval.

The frame carries the whole transcript with the input box at its end, so each
of those changed the height and moved every row below. Twice per call, which is
the input jumping up and down.

Nothing in the renderer was wrong: it was being handed a frame that really did
change size, twice, for no reason the user could see.
"""

from __future__ import annotations

from typing import Any

import pytest

from tau.message.types import AssistantMessage, TextContent, ToolCallContent
from tau.modes.interactive.agent_hooks import AgentHookHandler
from tau.modes.interactive.components.message_list import MessageList
from tau.tui.theme import MessageTheme
from tau.tui.utils import strip_ansi


class _Block:
    def invalidate(self) -> None: ...
    def set_streaming(self, value: bool) -> None: ...


class _Messages:
    def __init__(self) -> None:
        self.deferred: set[str] = set()

    def defer_tool_calls(self, ids) -> None:
        self.deferred.update(ids)


class _Layout:
    def __init__(self) -> None:
        self.messages = _Messages()

    class spinner:  # noqa: N801
        @staticmethod
        def set_streaming_estimate(_n: int) -> None: ...


class _Tui:
    def request_render(self) -> None: ...


def _handler(*, gate_active: bool) -> tuple[Any, _Layout]:
    handler = AgentHookHandler.__new__(AgentHookHandler)
    layout = _Layout()
    handler._layout = layout  # type: ignore[attr-defined]
    handler._tui = _Tui()  # type: ignore[attr-defined]
    handler._gate_active = gate_active  # type: ignore[attr-defined]
    handler._current_block = _Block()  # type: ignore[attr-defined]
    handler._pending_flush_handle = None  # type: ignore[attr-defined]
    handler._current_text_length = 0  # type: ignore[attr-defined]
    handler._last_flush_at = 0.0  # type: ignore[attr-defined]
    handler._update_block = lambda msg, streaming=False: None  # type: ignore[attr-defined]
    return handler, layout


def _streaming_message() -> AssistantMessage:
    """An assistant message whose tool-call arguments are still arriving."""
    return AssistantMessage(
        contents=[
            TextContent(content="Let me check."),
            ToolCallContent(id="call-1", name="terminal", args={"cmd": "ls -"}),
        ]
    )


def test_a_streamed_call_is_deferred_before_it_is_painted() -> None:
    handler, layout = _handler(gate_active=True)
    handler._pending_msg = _streaming_message()

    handler._flush_pending()

    assert "call-1" in layout.messages.deferred, "the call was painted, then hidden"


def test_nothing_is_deferred_without_a_gate() -> None:
    """No `tool_call` handler means no approval step, so nothing to wait for."""
    handler, layout = _handler(gate_active=False)
    handler._pending_msg = _streaming_message()

    handler._flush_pending()

    assert layout.messages.deferred == set()


def test_a_message_with_no_calls_defers_nothing() -> None:
    handler, layout = _handler(gate_active=True)
    handler._pending_msg = AssistantMessage(contents=[TextContent(content="just text")])

    handler._flush_pending()

    assert layout.messages.deferred == set()


def test_repeated_flushes_are_idempotent() -> None:
    """Streaming flushes many times per call; deferral must not accumulate noise."""
    handler, layout = _handler(gate_active=True)

    for _ in range(5):
        handler._pending_msg = _streaming_message()
        handler._flush_pending()

    assert layout.messages.deferred == {"call-1"}


class TestFrameHeight:
    """The symptom, measured: the block must not change size on its own."""

    def _messages(self) -> MessageList:
        messages = MessageList(height=20, theme=MessageTheme())
        messages.set_tool_lookup(lambda _name: None)
        return messages

    def test_a_deferred_call_is_absent_from_the_frame(self) -> None:
        messages = self._messages()
        messages.add_message(_streaming_message(), streaming=True)
        messages.defer_tool_calls(["call-1"])

        body = " ".join(strip_ansi(line) for line in messages.render(60))

        assert "Let me check." in body
        assert "ls -" not in body

    def test_height_is_stable_across_defer_then_reveal(self) -> None:
        """Deferring before the first paint means one transition, not three."""
        messages = self._messages()
        message = _streaming_message()
        messages.add_message(message, streaming=True)

        messages.defer_tool_calls(["call-1"])
        hidden = len(messages.render(60))
        hidden_again = len(messages.render(60))
        messages.reveal_tool_call("call-1")
        revealed = len(messages.render(60))

        assert hidden == hidden_again, "the frame changed height while merely hidden"
        assert revealed >= hidden, "revealing a call should only ever add rows"


@pytest.mark.parametrize("gate_active", [True, False])
def test_message_end_still_defers_as_a_backstop(gate_active: bool) -> None:
    """A provider that never streams delivers the call whole in the final event."""
    handler, layout = _handler(gate_active=gate_active)

    handler._defer_tool_calls(_streaming_message())

    assert ("call-1" in layout.messages.deferred) is True
