"""Tool calls awaiting permission must not render before they are resolved.

A tool call is drawn as part of the assistant message, which completes *before*
the engine's gate runs. So an approval prompt always landed underneath a call
that looked like it had already been made — the transcript read "ran it, then
asked".

These tests pin the deferral that fixes it, and just as importantly the escape
hatches: a call that is never executed (aborted turn, rolled-back message) has
to become visible again, because a tool call hidden forever is a far worse
failure than one shown a moment early.
"""

from __future__ import annotations

from typing import Any

import pytest

from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage, TextContent, ToolCallContent
from tau.modes.interactive.components.message_list import MessageList


def _assistant(*calls: tuple[str, str]) -> AssistantMessage:
    contents: list[Any] = [TextContent(content="let me check")]
    contents += [ToolCallContent(id=cid, name=name, args={"cmd": "ls"}) for cid, name in calls]
    return AssistantMessage(contents=contents)


def _rendered(messages: MessageList, width: int = 80) -> str:
    return "\n".join(line for block in messages._blocks for line in block.render(width))


# ── Hooks.has_handlers ───────────────────────────────────────────────────────


class TestHasHandlers:
    def test_false_with_nothing_registered(self) -> None:
        assert Hooks().has_handlers("tool_call") is False

    def test_true_once_a_handler_is_registered(self) -> None:
        hooks = Hooks()

        @hooks.on("tool_call")
        async def _gate(event, ctx=None):
            return None

        assert hooks.has_handlers("tool_call") is True

    def test_unregistering_the_last_handler_flips_it_back(self) -> None:
        hooks = Hooks()

        async def _gate(event, ctx=None):
            return None

        hooks.register("tool_call", _gate)
        hooks.unregister("tool_call", _gate)
        assert hooks.has_handlers("tool_call") is False

    def test_a_catch_all_subscriber_does_not_count(self) -> None:
        # Subscribers observe everything and can never change an outcome, so
        # their presence says nothing about whether calls are being gated.
        hooks = Hooks()

        async def _listener(event):
            return None

        hooks.subscribe(_listener)
        assert hooks.has_handlers("tool_call") is False


# ── Rendering ────────────────────────────────────────────────────────────────


class TestDeferredRendering:
    def test_a_call_renders_normally_when_nothing_is_deferred(self) -> None:
        messages = MessageList()
        messages.add_message(_assistant(("c1", "terminal")))

        assert "terminal" in _rendered(messages).lower()

    def test_a_deferred_call_is_hidden(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1"])
        messages.add_message(_assistant(("c1", "terminal")))

        out = _rendered(messages)
        assert "terminal" not in out.lower()
        # The assistant's prose is unaffected — only the call is withheld.
        assert "let me check" in out

    def test_revealing_shows_it(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1"])
        messages.add_message(_assistant(("c1", "terminal")))
        assert "terminal" not in _rendered(messages).lower()

        assert messages.reveal_tool_call("c1") is True
        assert "terminal" in _rendered(messages).lower()

    def test_revealing_only_affects_the_named_call(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1", "c2"])
        messages.add_message(_assistant(("c1", "terminal"), ("c2", "read")))

        messages.reveal_tool_call("c1")

        out = _rendered(messages).lower()
        assert "terminal" in out
        assert "read" not in out

    def test_revealing_an_unknown_id_reports_no_change(self) -> None:
        messages = MessageList()
        assert messages.reveal_tool_call("nope") is False

    def test_revealing_twice_is_harmless(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1"])
        messages.add_message(_assistant(("c1", "terminal")))

        assert messages.reveal_tool_call("c1") is True
        assert messages.reveal_tool_call("c1") is False

    def test_clearing_reveals_everything_still_hidden(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1", "c2"])
        messages.add_message(_assistant(("c1", "terminal"), ("c2", "read")))

        messages.clear_deferred_tool_calls()

        out = _rendered(messages).lower()
        assert "terminal" in out and "read" in out

    def test_clearing_with_nothing_deferred_is_a_no_op(self) -> None:
        messages = MessageList()
        messages.add_message(_assistant(("c1", "terminal")))
        messages.clear_deferred_tool_calls()
        assert "terminal" in _rendered(messages).lower()

    def test_deferring_after_the_block_exists_still_hides(self) -> None:
        # The set is shared by reference, so a later defer reaches the block
        # that was already built.
        messages = MessageList()
        block = messages.add_message(_assistant(("c1", "terminal")))
        messages.defer_tool_calls(["c1"])
        block.invalidate()

        assert "terminal" not in _rendered(messages).lower()

    def test_blocks_built_by_build_blocks_share_the_same_state(self) -> None:
        messages = MessageList()
        messages.defer_tool_calls(["c1"])

        built = messages.build_blocks([_assistant(("c1", "terminal"))])

        out = "\n".join(line for block in built for line in block.render(80))
        assert "terminal" not in out.lower()


# ── Handler wiring ───────────────────────────────────────────────────────────


class _Spinner:
    def __init__(self) -> None:
        self.theme = type("T", (), {"label_tool_calling": "Running", "label_working": "Working"})()

    def set_label(self, *_a: Any) -> None:
        pass


class _Layout:
    def __init__(self) -> None:
        self.messages = MessageList()
        self.spinner = _Spinner()


class _TUI:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


def _handler() -> Any:
    from tau.modes.interactive.agent_hooks import AgentHookHandler

    handler = AgentHookHandler.__new__(AgentHookHandler)
    handler._layout = _Layout()  # type: ignore[attr-defined]
    handler._tui = _TUI()  # type: ignore[attr-defined]
    handler._tool_names = {}  # type: ignore[attr-defined]
    handler._gate_active = True  # type: ignore[attr-defined]
    return handler


class TestHandlerWiring:
    def test_message_end_defers_every_call_it_makes(self) -> None:
        handler = _handler()
        handler._defer_tool_calls(_assistant(("c1", "terminal"), ("c2", "read")))

        assert handler._layout.messages._deferred_tool_calls == {"c1", "c2"}

    def test_a_message_without_calls_defers_nothing(self) -> None:
        handler = _handler()
        handler._defer_tool_calls(AssistantMessage(contents=[TextContent(content="hi")]))

        assert handler._layout.messages._deferred_tool_calls == set()

    def test_reveal_requests_a_render_only_when_something_changed(self) -> None:
        handler = _handler()
        handler._layout.messages.defer_tool_calls(["c1"])
        handler._layout.messages.add_message(_assistant(("c1", "terminal")))

        handler._reveal_tool_call("c1")
        assert handler._tui.renders == 1

        handler._reveal_tool_call("c1")  # already revealed
        assert handler._tui.renders == 1

    def test_reveal_tolerates_a_missing_id(self) -> None:
        handler = _handler()
        handler._reveal_tool_call(None)  # must not raise


@pytest.mark.asyncio
async def test_a_blocked_call_is_revealed_by_tool_execution_end() -> None:
    # A denied call never emits tool_execution_start — the engine returns the
    # gate's result and goes straight to the end event — so this is its only
    # chance to become visible.
    from tau.message.types import ToolResultContent

    handler = _handler()
    handler._layout.messages.defer_tool_calls(["c1"])
    handler._layout.messages.add_message(_assistant(("c1", "terminal")))

    event = type(
        "E",
        (),
        {"tool_result": ToolResultContent(id="c1", content="Denied", is_error=True)},
    )()
    await handler._on_tool_end(event)

    assert "terminal" in _rendered(handler._layout.messages).lower()
