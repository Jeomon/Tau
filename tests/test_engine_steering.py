"""Engine-loop tests for steering / follow-up injection and pending-queue display sync.

Covers the two bugs fixed in tau/engine/service.py:

1. Display sync — a ``QueueUpdateEvent`` is emitted when steering / follow-up
   messages are *consumed* (dequeued), not only when they are enqueued, so the
   TUI's pending-queue hint clears the moment a message lands in history.

2. Steering on a plain-text turn — the loop re-polls steering after every turn and
   its condition keeps running while messages are pending, so a steer that arrives
   when the turn ends with a plain text answer is injected and drives another turn
   instead of being stranded in the queue and never sent to the model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tau.engine.service import Engine
from tau.engine.types import EngineContext, EngineOptions
from tau.inference.types import (
    EndEvent,
    StopReason,
    TextEndEvent,
    ToolCallEndEvent,
)
from tau.message.types import LLMMessage, TextContent, ToolCallContent, UserMessage


def run(coro):
    return asyncio.run(coro)


# ── Scripted fake LLM ──────────────────────────────────────────────────────────


class _Model:
    name = "fake-model"


class _Options:
    headers: dict = {}


class _Api:
    options = _Options()


class ScriptedLLM:
    """Replays a pre-programmed list of turns; each turn is a list of stream events.

    Records the message context handed to every ``stream()`` call so tests can
    assert what the model actually saw, and counts calls so tests can assert the
    loop continued (or stopped) as expected.
    """

    def __init__(self, turns: list[list]):
        self._turns = list(turns)
        self.calls = 0
        self.contexts: list[list] = []
        self.ephemeral_counts: list[int] = []
        self.model = _Model()
        self.api = _Api()
        self.provider_id = "fake-provider"
        # Optional per-turn async hooks, invoked just before that turn streams.
        # Used to simulate a user steering *during* a specific turn.
        self.on_turn: list = []

    def stream(self, ctx):
        return self._gen(ctx)

    async def _gen(self, ctx):
        idx = self.calls
        self.calls += 1
        self.contexts.append(list(ctx.messages))
        self.ephemeral_counts.append(ctx.ephemeral_message_count)
        hook = self.on_turn[idx] if idx < len(self.on_turn) else None
        if hook is not None:
            await hook()
        # Default to a clean Stop once the script is exhausted, so a loop that
        # keeps draining an empty queue terminates instead of hanging.
        turn = self._turns.pop(0) if self._turns else [EndEvent(reason=StopReason.Stop)]
        for ev in turn:
            yield ev


def _text_turn(text: str = "ok") -> list:
    return [TextEndEvent(text=TextContent(content=text)), EndEvent(reason=StopReason.Stop)]


def _tool_turn(name: str = "missing_tool") -> list:
    # An unregistered tool yields an error ToolResult but still drives the
    # StopReason.ToolCalls branch — no real tool needed.
    return [
        ToolCallEndEvent(tool_call=ToolCallContent(id="tc1", name=name, args={})),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def _make_engine(turns: list[list]) -> tuple[Engine, ScriptedLLM, list]:
    llm = ScriptedLLM(turns)
    engine = Engine(cwd=Path("."), llm=llm, tools=[], system_prompt="")  # type: ignore[arg-type]
    events: list = []
    engine.hooks.subscribe(lambda e: events.append(e))
    return engine, llm, events


def _texts(message) -> str:
    return "".join(
        c.content for c in getattr(message, "contents", []) if isinstance(c, TextContent)
    )


def _queue_updates(events: list, queue: str) -> list:
    return [e for e in events if getattr(e, "type", None) == "queue_update" and e.queue == queue]


def test_context_callbacks_rewrite_and_append_ephemeral_messages() -> None:
    history: list[LLMMessage] = [UserMessage.from_text("original")]
    engine, llm, _events = _make_engine([_text_turn()])

    async def transform_context(
        _messages: list[LLMMessage], _signal: asyncio.Event | None
    ) -> list[LLMMessage]:
        return [UserMessage.from_text("rewritten")]

    async def ephemeral_injection() -> list[UserMessage]:
        return [UserMessage.from_text("browser state")]

    engine.options = EngineOptions(
        transform_context=transform_context,
        ephemeral_injection=ephemeral_injection,
    )

    run(engine.run(EngineContext(system_prompt="", messages=history)))

    assert [_texts(message) for message in llm.contexts[0]] == [
        "rewritten",
        "browser state",
    ]
    assert llm.ephemeral_counts == [1]
    assert _texts(history[0]) == "original"
    assert not any(_texts(message) == "browser state" for message in history)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestSteeringAtStop:
    """Steering pending when a turn ends on Stop must be injected and seen."""

    def test_steering_at_stop_is_injected_and_model_re_invoked(self):
        # Turn 1 answers with plain text (Stop) — no tool call follows, which is
        # exactly the case that used to strand the steer. The steer arrives *during*
        # turn 1, so it must be picked up and drive a second turn.
        history: list[LLMMessage] = [UserMessage.from_text("check weather")]
        engine, llm, events = _make_engine([_text_turn("weather is 26C"), _text_turn("i am fine")])

        async def steer_mid_turn():
            await engine.steer(UserMessage.from_text("how are you"))

        # Fire during turn 1 (index 0); the re-poll after turn 1 should drain it.
        llm.on_turn = [steer_mid_turn]

        async def _test():
            await engine.run(EngineContext(system_prompt="", messages=history))

        run(_test())

        # The model was called a second time (the turn continued past Stop)...
        assert llm.calls == 2
        # ...and that second call's context contained the steered message.
        assert any(_texts(m) == "how are you" for m in llm.contexts[1])
        # The steer is now part of history, and the queue is drained.
        assert any(_texts(m) == "how are you" for m in history)
        assert engine.state.steering_queue is not None and engine.state.steering_queue.is_empty()

    def test_steering_at_stop_emits_queue_update_clearing_display(self):
        history: list[LLMMessage] = [UserMessage.from_text("hi")]
        engine, llm, events = _make_engine([_text_turn(), _text_turn()])

        async def steer_mid_turn():
            await engine.steer(UserMessage.from_text("steer me"))

        llm.on_turn = [steer_mid_turn]

        async def _test():
            await engine.run(EngineContext(system_prompt="", messages=history))

        run(_test())

        # A steering queue_update with an empty snapshot is emitted on dequeue,
        # which drives the TUI to clear the pending hint.
        drains = [e for e in _queue_updates(events, "steering") if not e.messages]
        assert drains, "expected a steering queue_update with empty snapshot after dequeue"

    def test_successful_run_clears_error_from_previous_run(self):
        history: list[LLMMessage] = [UserMessage.from_text("hi")]
        engine, _, _ = _make_engine([_text_turn()])
        engine.state.error_message = "stale failure"

        run(engine.run(EngineContext(system_prompt="", messages=history)))

        assert engine.state.error_message is None


class TestSteeringAtToolCalls:
    """Steering consumed mid-task (the original supported path) still works."""

    def test_steering_injected_after_tool_round_trip(self):
        history: list[LLMMessage] = [UserMessage.from_text("do a thing")]
        # Turn 1 = tool call (ToolCalls); steer arrives during it → re-polled after
        # the tool round-trip → injected before Turn 2 = Stop.
        engine, llm, events = _make_engine([_tool_turn(), _text_turn("done")])

        async def steer_mid_turn():
            await engine.steer(UserMessage.from_text("also do this"))

        llm.on_turn = [steer_mid_turn]

        async def _test():
            await engine.run(EngineContext(system_prompt="", messages=history))

        run(_test())

        assert llm.calls == 2
        assert any(_texts(m) == "also do this" for m in history)
        assert engine.state.steering_queue is not None and engine.state.steering_queue.is_empty()
        drains = [e for e in _queue_updates(events, "steering") if not e.messages]
        assert drains, "expected a steering queue_update with empty snapshot after dequeue"


class TestFollowupAtStop:
    """Follow-up draining at Stop (pre-existing behaviour) is preserved."""

    def test_followup_at_stop_is_injected(self):
        history: list[LLMMessage] = [UserMessage.from_text("hi")]
        engine, llm, events = _make_engine([_text_turn()])

        async def _test():
            await engine.follow_up(UserMessage.from_text("and then this"))
            await engine.run(EngineContext(system_prompt="", messages=history))

        run(_test())

        assert llm.calls == 2
        assert any(_texts(m) == "and then this" for m in history)
        assert engine.state.follow_up_queue is not None and engine.state.follow_up_queue.is_empty()
        drains = [e for e in _queue_updates(events, "followup") if not e.messages]
        assert drains, "expected a followup queue_update with empty snapshot after dequeue"


class TestRunContinueDrainsLateSteering:
    """A steer that lands after the loop already stopped is drained by run_continue.

    This is the engine-level mechanism Agent.invoke() now relies on: the submit→
    steer hop is a separate task, so a steer can enqueue just past the turn's final
    queue check. run_continue() must surface it (emit it so the UI renders it) and
    run another turn so the model actually responds.
    """

    def test_late_steer_is_emitted_and_answered(self):
        history: list[LLMMessage] = [UserMessage.from_text("weather in tokyo")]
        # Turn 1 answers Tokyo and stops; turn 2 (the continuation) answers kochi.
        engine, llm, events = _make_engine([_text_turn("tokyo 28C"), _text_turn("kochi 26C")])

        async def _test():
            # Turn 1 runs to completion with nothing queued.
            await engine.run(EngineContext(system_prompt="", messages=history))
            assert llm.calls == 1
            # Steer lands *after* the turn stopped (the race the fix targets).
            await engine.steer(UserMessage.from_text("also in kochi"))
            # Drain it via continuation, the path Agent.invoke() drives.
            await engine.run_continue()

        run(_test())

        # The continuation ran a second model turn...
        assert llm.calls == 2
        # ...the late steer was emitted as a real user message (so the UI renders
        # it), is in engine history, and its text reached the model's context.
        assert any(_texts(m) == "also in kochi" for m in engine.state.messages)
        assert any(_texts(m) == "also in kochi" for m in llm.contexts[1])
        starts = [
            e
            for e in events
            if getattr(e, "type", None) == "message_start"
            and _texts(getattr(e, "message", None)) == "also in kochi"
        ]
        assert starts, "expected the late steer to be emitted as a message_start"
        assert engine.state.steering_queue is not None and engine.state.steering_queue.is_empty()

        # The initial user message is passed as context and never re-emitted, so a
        # session listener on message_end only sees engine-injected messages — i.e.
        # persisting injected user messages can't double-write the initial one.
        initial_starts = [
            e
            for e in events
            if getattr(e, "type", None) == "message_start"
            and _texts(getattr(e, "message", None)) == "weather in tokyo"
        ]
        assert not initial_starts, "context messages must not be re-emitted"


class TestNoQueuedMessages:
    """A plain turn with nothing queued runs exactly once and stops."""

    def test_single_turn_no_continuation(self):
        history: list[LLMMessage] = [UserMessage.from_text("hi")]
        engine, llm, events = _make_engine([_text_turn()])

        async def _test():
            await engine.run(EngineContext(system_prompt="", messages=history))

        run(_test())

        assert llm.calls == 1
        assert not _queue_updates(events, "steering")
