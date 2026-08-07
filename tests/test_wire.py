"""The JSON-lines wire layer shared by `--mode rpc` and `-p --mode json`.

These two modes used to carry their own copies of everything here, and drifted:
the JSON mode learned to emit deltas and RPC did not, while RPC learned to
survive un-encodable fields, guard stdout and apply backpressure and the JSON
mode did not. The tests below pin the behaviour itself; the ones at the bottom
pin that both modes actually route through this module rather than growing
private copies again.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path

from tau.hooks.engine import ToolExecutionEndEvent
from tau.message.types import AssistantMessage, TextContent, ThinkingContent, ToolResultContent
from tau.modes import wire


def _message(text: str = "", thinking: str = "") -> AssistantMessage:
    contents: list = []
    if thinking:
        contents.append(ThinkingContent(content=thinking))
    if text:
        contents.append(TextContent(content=text))
    return AssistantMessage(contents=contents)


# ── Streaming deltas ─────────────────────────────────────────────────────────


class TestStreamDeltas:
    def test_each_tick_carries_only_what_was_appended(self):
        deltas = wire.StreamDeltas()

        first = deltas.annotate({}, _message("Let me"))
        second = deltas.annotate({}, _message("Let me check."))

        assert first["delta"] == "Let me"
        assert second["delta"] == " check."

    def test_thinking_is_tracked_separately_from_text(self):
        deltas = wire.StreamDeltas()

        deltas.annotate({}, _message(thinking="hmm"))
        payload = deltas.annotate({}, _message(text="hi", thinking="hmm, ok"))

        assert payload["thinking_delta"] == ", ok"
        assert payload["delta"] == "hi"

    def test_a_rewritten_block_resends_the_whole_text(self):
        """TextEndEvent replaces a streaming block outright, so the accumulated
        prefix does not always hold and appending blindly would corrupt the
        client's copy."""
        deltas = wire.StreamDeltas()

        deltas.annotate({}, _message("draft"))
        payload = deltas.annotate({}, _message("final answer"))

        assert payload["delta"] == "final answer"

    def test_an_unchanged_message_carries_no_delta_key(self):
        deltas = wire.StreamDeltas()
        deltas.annotate({}, _message("same"))

        assert deltas.annotate({}, _message("same")) == {}

    def test_omit_message_drops_the_cumulative_copy(self):
        deltas = wire.StreamDeltas()
        assert "message" in deltas.annotate({"message": {"x": 1}}, _message("a"))

        deltas.omit_message = True
        payload = deltas.annotate({"message": {"x": 1}}, _message("ab"))

        assert "message" not in payload
        assert payload["delta"] == "b"

    def test_apply_resets_on_message_start(self):
        """Deltas are relative to the current message, not the previous one."""
        deltas = wire.StreamDeltas()
        deltas.apply({"type": "message_update"}, _MessageEvent(_message("first reply")))

        deltas.apply({"type": "message_start"}, _MessageEvent(_message()))
        payload = deltas.apply({"type": "message_update"}, _MessageEvent(_message("second")))

        assert payload["delta"] == "second"

    def test_apply_leaves_other_events_alone(self):
        deltas = wire.StreamDeltas(omit_message=True)
        payload = {"type": "message_end", "message": {"x": 1}}

        assert deltas.apply(payload, _MessageEvent(_message("done"))) == payload


class _MessageEvent:
    def __init__(self, message) -> None:
        self.message = message


# ── Encoding ─────────────────────────────────────────────────────────────────


class _Colour(enum.Enum):
    RED = "red"


@dataclasses.dataclass
class _Plain:
    type: str = "plain"
    value: int = 1


class TestEncoding:
    def test_a_tool_result_carrying_bytes_still_encodes(self):
        """A screenshot tool used to take the whole event down: plain
        json.dumps raises on bytes, and Hooks.emit swallows the exception, so
        the consumer silently never saw that tool result."""
        result = ToolResultContent(id="call_1", content="done", tool_name="screenshot")
        result.image = b"\x89PNG\r\n\x1a\n"  # type: ignore[assignment]

        line = wire.encode_line(wire.serialize_event(ToolExecutionEndEvent(tool_result=result)))

        assert json.loads(line)["tool_result"]["image"] == "iVBORw0KGgo="

    def test_exotic_values_degrade_instead_of_raising(self):
        payload = {
            "enum": _Colour.RED,
            "path": Path("/tmp/x"),
            "set": {1},
            "bytes": b"ab",
            "other": object(),
        }

        decoded = json.loads(wire.encode_line(payload))

        assert decoded["enum"] == "red"
        assert decoded["path"] == "/tmp/x"
        assert decoded["set"] == [1]
        assert decoded["bytes"] == "YWI="
        assert isinstance(decoded["other"], str)

    def test_a_dataclass_event_keeps_its_field_names(self):
        assert wire.serialize_event(_Plain()) == {"type": "plain", "value": 1}

    def test_a_non_dataclass_event_keeps_its_payload(self):
        class _Custom:
            type = "custom"

            def __init__(self) -> None:
                self.visible = 1
                self._hidden = 2

        assert wire.serialize_event(_Custom()) == {"type": "custom", "visible": 1}

    def test_an_undeepcopyable_dataclass_degrades_to_a_shallow_dict(self):
        """asdict() deep-copies; a field that refuses must not drop the event."""

        class _Hostile:
            def __deepcopy__(self, memo):
                raise RuntimeError("nope")

        @dataclasses.dataclass
        class _Event:
            type: str = "hostile"
            payload: object = None

        event = _Event(payload=_Hostile())

        out = wire.serialize_event(event)

        assert out["type"] == "hostile"
        assert isinstance(out["payload"], _Hostile)


# ── Event coverage ───────────────────────────────────────────────────────────


class TestForwardedEvents:
    def test_message_rollback_is_forwarded(self):
        """A client that never sees it drifts out of sync with the session
        after an interrupted tool turn."""
        assert "message_rollback" in wire.FORWARDED_EVENTS

    def test_the_lists_have_no_duplicates(self):
        for events in (wire.FORWARDED_EVENTS, wire.COMPACT_EVENTS):
            assert len(set(events)) == len(events)

    def test_compact_is_a_subset_of_full(self):
        assert set(wire.COMPACT_EVENTS) < set(wire.FORWARDED_EVENTS)

    def test_message_rollback_is_in_the_compact_set_too(self):
        """Not a verbosity choice: without it a consumer's transcript silently
        diverges from the session file, so it stays in the default even though
        everything else added alongside it is opt-in."""
        assert "message_rollback" in wire.COMPACT_EVENTS

    def test_compact_is_otherwise_the_historical_json_set(self):
        """`-p --mode json` consumers written before the shared layer see one
        new event type, not thirteen."""
        historical = {
            "agent_start",
            "agent_end",
            "message_start",
            "message_update",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "agent_error",
            "settled",
        }

        assert set(wire.COMPACT_EVENTS) - historical == {"message_rollback"}

    def test_both_sets_are_selectable_by_name(self):
        assert wire.EVENT_SETS == {
            "compact": wire.COMPACT_EVENTS,
            "full": wire.FORWARDED_EVENTS,
        }


# ── Both modes use this module ───────────────────────────────────────────────


class TestBothModesShareTheLayer:
    """The point of the module: a fix lands in both modes or neither."""

    def test_rpc_mode_delegates_to_wire(self):
        import tau.modes.rpc.mode as rpc_mode

        assert rpc_mode._serialize_event is wire.serialize_event
        assert rpc_mode._json_default is wire.json_default
        assert rpc_mode._write is wire.write
        assert rpc_mode._FORWARDED_EVENTS is wire.FORWARDED_EVENTS
        assert rpc_mode._OUTPUT is wire.OUTPUT

    def test_the_json_mode_has_no_private_serializer_left(self):
        """It used to hand-roll _serialize/_update_payload/_appended, which is
        how it ended up with deltas RPC lacked and no bytes handling."""
        import inspect

        from tau.modes.print import mode as print_mode

        source = inspect.getsource(print_mode._run_json)

        assert "wire.serialize_event" in source
        assert "wire.EVENT_SETS" in source
        assert "json.dumps" not in source
        assert "dataclasses.asdict" not in source

    def test_the_json_mode_has_no_hand_written_event_list(self):
        """It listed 9 of the 22 by hand and silently omitted message_rollback.
        Both sets it can select from now live in wire."""
        import inspect

        from tau.modes.print import mode as print_mode

        assert "hook_names" not in inspect.getsource(print_mode._run_json)

    def test_the_stdout_guard_covers_both_protocol_modes(self):
        """A stray print from a tool corrupts either stream, not just RPC's."""
        import inspect

        from tau.console import cli

        source = inspect.getsource(cli._start)

        assert '("rpc", "json")' in source
