"""Tests for JSON mode's event stream (``tau --mode json``), in console/cli.py.

Regression context: ``message_update`` fires once per streamed token and its
``message`` field is the *whole accumulated message*, mutated in place by the
engine. Serializing it verbatim made stdout grow with the square of the reply
length — a 188 KB answer emitted ~1.5 GB. JSON mode now emits only what was
appended since the previous update; ``message_end`` still carries the full
message, so no information is lost across the stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from tau.hooks.engine import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    SettledEvent,
)
from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage, TextContent, ThinkingContent
from tau.modes.print.mode import _run_json

# One scripted stream step: mutate the message, then hand back the event to emit.
Step = Callable[[], object]


class _FakeRuntime:
    """Drives _run_json with a scripted event sequence instead of a real model.

    Steps are callables, not pre-built events: the engine mutates the message
    in place and emits immediately, so a handler sees the state *at that
    moment*. Building every event up front would instead let all of them
    observe the final message, which is not what the real stream looks like.
    """

    def __init__(self, steps):
        self.hooks = Hooks()
        self._steps = steps

    async def invoke(self, _message):
        for step in self._steps:
            await self.hooks.emit(step())


def _run(steps, capsys):
    asyncio.run(_run_json(_FakeRuntime(steps), ["prompt"], "compact"))
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()], out


def _streaming_text(chunks):
    """A message_start, one message_update per chunk, message_end, settled.

    Mirrors the engine: a single TextContent block is appended to in place and
    the same message object is re-emitted each time (engine/service.py:847-855).
    """
    message = AssistantMessage(contents=[])
    block = TextContent(content="")
    message.contents.append(block)

    def append(chunk):
        block.content += chunk
        return MessageUpdateEvent(message=message)

    steps: list[Step] = [lambda: MessageStartEvent(message=message)]
    steps.extend(lambda c=chunk: append(c) for chunk in chunks)
    steps.append(lambda: MessageEndEvent(message=message))
    steps.append(lambda: SettledEvent())
    return steps


class TestMessageUpdateDeltas:
    def test_update_carries_delta_not_accumulated_message(self, capsys):
        events, _ = _run(_streaming_text(["Hello", " world"]), capsys)
        updates = [e for e in events if e["type"] == "message_update"]
        assert [u["delta"] for u in updates] == ["Hello", " world"]
        assert all("message" not in u for u in updates)

    def test_deltas_reconstruct_the_full_reply(self, capsys):
        chunks = ["The ", "quick ", "brown ", "fox"]
        events, _ = _run(_streaming_text(chunks), capsys)
        joined = "".join(
            e["delta"] for e in events if e["type"] == "message_update" and "delta" in e
        )
        assert joined == "".join(chunks)

    def test_message_end_still_carries_the_full_message(self, capsys):
        events, _ = _run(_streaming_text(["all ", "of ", "it"]), capsys)
        end = next(e for e in events if e["type"] == "message_end")
        assert end["message"]["contents"][0]["content"] == "all of it"

    def test_settled_terminates_the_stream(self, capsys):
        events, _ = _run(_streaming_text(["x"]), capsys)
        assert events[-1]["type"] == "settled"

    def test_no_delta_field_when_nothing_was_appended(self, capsys):
        """Tool-call updates re-emit the message without adding text."""
        message = AssistantMessage(contents=[TextContent(content="done")])
        events, _ = _run(
            [
                lambda: MessageStartEvent(message=message),
                lambda: MessageUpdateEvent(message=message),  # carries the delta
                lambda: MessageUpdateEvent(message=message),  # nothing new
                lambda: SettledEvent(),
            ],
            capsys,
        )
        updates = [e for e in events if e["type"] == "message_update"]
        assert updates[0]["delta"] == "done"
        assert "delta" not in updates[1]

    def test_thinking_streams_separately_from_text(self, capsys):
        message = AssistantMessage(contents=[])
        thinking = ThinkingContent(content="")
        text = TextContent(content="")
        message.contents.extend([thinking, text])

        def think():
            thinking.content += "pondering"
            return MessageUpdateEvent(message=message)

        def answer():
            text.content += "answer"
            return MessageUpdateEvent(message=message)

        steps: list[Step] = [
            lambda: MessageStartEvent(message=message),
            think,
            answer,
            lambda: SettledEvent(),
        ]
        parsed, _ = _run(steps, capsys)
        updates = [e for e in parsed if e["type"] == "message_update"]
        assert updates[0]["thinking_delta"] == "pondering"
        assert "delta" not in updates[0]
        assert updates[1]["delta"] == "answer"
        assert "thinking_delta" not in updates[1]

    def test_block_rewritten_wholesale_is_not_duplicated(self, capsys):
        """TextEndEvent replaces a streaming block's content instead of appending.

        The delta logic must fall back to the full value when the previous text
        is no longer a prefix, rather than emitting a bogus suffix.
        """
        message = AssistantMessage(contents=[])
        block = TextContent(content="strea")
        message.contents.append(block)

        def rewrite():
            block.content = "completely different"  # wholesale replacement
            return MessageUpdateEvent(message=message)

        steps: list[Step] = [
            lambda: MessageStartEvent(message=message),
            lambda: MessageUpdateEvent(message=message),
            rewrite,
            lambda: SettledEvent(),
        ]
        parsed, _ = _run(steps, capsys)
        updates = [e for e in parsed if e["type"] == "message_update"]
        assert updates[0]["delta"] == "strea"
        assert updates[1]["delta"] == "completely different"

    def test_state_resets_between_messages(self, capsys):
        """A second message starting with the same prefix must not be swallowed."""
        first = AssistantMessage(contents=[TextContent(content="same")])
        second = AssistantMessage(contents=[TextContent(content="same")])
        parsed, _ = _run(
            [
                lambda: MessageStartEvent(message=first),
                lambda: MessageUpdateEvent(message=first),
                lambda: MessageEndEvent(message=first),
                lambda: MessageStartEvent(message=second),
                lambda: MessageUpdateEvent(message=second),
                lambda: MessageEndEvent(message=second),
                lambda: SettledEvent(),
            ],
            capsys,
        )
        updates = [e for e in parsed if e["type"] == "message_update"]
        assert [u["delta"] for u in updates] == ["same", "same"]


class TestOutputSizeIsLinear:
    """The actual regression: stdout must scale with the reply, not its square."""

    def _emitted_bytes(self, n_chunks, capsys):
        chunk = "hello world "
        _, out = _run(_streaming_text([chunk] * n_chunks), capsys)
        return len(out), n_chunks * len(chunk)

    def test_doubling_the_reply_roughly_doubles_the_output(self, capsys):
        small, _ = self._emitted_bytes(500, capsys)
        large, _ = self._emitted_bytes(1000, capsys)
        # Quadratic growth would put this at ~4x; linear lands just above 2x
        # because message_end's full copy is a fixed additive term.
        assert large / small < 2.6, f"output scaled {large / small:.2f}x — not linear"

    def test_total_output_is_a_small_multiple_of_the_reply(self, capsys):
        emitted, reply_size = self._emitted_bytes(2000, capsys)
        # Pre-fix this was ~1000x the reply size (23 KB reply -> 23.9 MB).
        assert emitted < reply_size * 6, (
            f"emitted {emitted} bytes for a {reply_size}-byte reply ({emitted / reply_size:.0f}x)"
        )
