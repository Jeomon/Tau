"""Tests for tau/rpc/mode.py — _write, _serialize_event, RpcExtensionUIContext."""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

import tau.modes.rpc.mode as mode
from tau.modes.rpc.mode import (
    RpcExtensionUIContext,
    _extension_error_payload,
    _json_default,
    _serialize_event,
    _start_prompt,
    _write,
)


def capture_write(fn, *args, **kwargs):
    """Call fn capturing everything written to stdout; return (result, lines)."""
    buf = StringIO()
    old, sys.stdout = sys.stdout, buf
    try:
        result = fn(*args, **kwargs)
    finally:
        sys.stdout = old
    output = buf.getvalue()
    lines = [ln for ln in output.splitlines() if ln]
    return result, lines


class TestWrite:
    def test_writes_json_line(self):
        _, lines = capture_write(_write, {"type": "ping"})
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"type": "ping"}

    def test_writes_multiple_fields(self):
        payload = {"type": "event", "id": "abc", "data": 42}
        _, lines = capture_write(_write, payload)
        assert json.loads(lines[0]) == payload

    def test_write_empty_dict(self):
        _, lines = capture_write(_write, {})
        assert json.loads(lines[0]) == {}

    def test_newline_terminated(self):
        buf = StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            _write({"x": 1})
        finally:
            sys.stdout = old
        assert buf.getvalue().endswith("\n")


class TestSerializeEvent:
    def test_dataclass_converted_to_dict(self):
        @dataclasses.dataclass
        class MyEvent:
            type: str
            value: int

        e = MyEvent(type="test", value=7)
        result = _serialize_event(e)
        assert result == {"type": "test", "value": 7}

    def test_non_dataclass_uses_class_name(self):
        class FakeEvent:
            pass

        result = _serialize_event(FakeEvent())
        assert result == {"type": "FakeEvent"}

    def test_nested_dataclass(self):
        @dataclasses.dataclass
        class Inner:
            x: int

        @dataclasses.dataclass
        class Outer:
            type: str
            inner: Inner

        result = _serialize_event(Outer(type="outer", inner=Inner(x=5)))
        assert result["inner"] == {"x": 5}

    def test_plain_string_is_not_dataclass(self):
        result = _serialize_event("hello")
        assert result == {"type": "str"}


class TestSerializeEventRobustness:
    def test_non_dataclass_keeps_payload(self):
        class Evt:
            def __init__(self):
                self.type = "custom_event"
                self.count = 3
                self._private = "hidden"

        assert _serialize_event(Evt()) == {"type": "custom_event", "count": 3}

    def test_non_dataclass_without_type_attr_uses_class_name(self):
        class Evt:
            def __init__(self):
                self.count = 1

        assert _serialize_event(Evt()) == {"type": "Evt", "count": 1}

    def test_undeepcopyable_dataclass_degrades_to_shallow_dict(self):
        class NoCopy:
            def __deepcopy__(self, memo):
                raise TypeError("cannot copy")

        payload = NoCopy()

        @dataclasses.dataclass
        class Evt:
            type: str
            blob: object

        result = _serialize_event(Evt(type="e", blob=payload))
        assert result["type"] == "e"
        assert result["blob"] is payload  # shallow: kept by reference, not dropped


class TestJsonSafety:
    def test_default_encodes_exotic_values(self):
        class Colour(enum.Enum):
            RED = "red"

        assert _json_default(Colour.RED) == "red"
        assert _json_default(Path("/tmp/x")) == "/tmp/x"
        assert _json_default(b"ab") == "YWI="
        assert _json_default({1, 2}) in ([1, 2], [2, 1])

    def test_write_survives_unserializable_field(self):
        class Weird:
            def __repr__(self):
                return "<weird>"

        _, lines = capture_write(_write, {"type": "e", "value": Weird()})
        assert json.loads(lines[0]) == {"type": "e", "value": "<weird>"}


class TestExtensionErrorPayload:
    def test_shape_matches_protocol(self):
        @dataclasses.dataclass
        class Err:
            extension_path: str = "/x/ext.py"
            event: str = "agent_start"
            error: str = "boom"
            stack: str = "Traceback…"

        assert _extension_error_payload(Err()) == {
            "type": "extension_error",
            "extensionPath": "/x/ext.py",
            "event": "agent_start",
            "error": "boom",
            "stack": "Traceback…",
        }


class _FakeHooks:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def register(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

        def _unsub() -> None:
            self.handlers[name].remove(callback)

        return _unsub

    async def emit(self, name):
        for cb in list(self.handlers.get(name, [])):
            await cb(object())


class TestStartPrompt:
    """The prompt ack means 'accepted and started', not 'turn finished'."""

    @pytest.mark.asyncio
    async def test_acks_once_the_turn_starts_while_it_still_runs(self):
        hooks = _FakeHooks()
        finished = asyncio.Event()
        acked: list = []

        class _Runtime:
            def __init__(self):
                self.hooks = hooks
                self.done = False

            async def invoke(self, text, options=None):
                await hooks.emit("agent_start")
                await finished.wait()
                self.done = True

        rt = _Runtime()
        await _start_prompt(rt, "hi", None, lambda: acked.append("ok"), lambda e: acked.append(e))

        assert acked == ["ok"]
        assert rt.done is False  # ack arrived mid-turn
        assert hooks.handlers.get("agent_start") == []  # hook cleaned up

        finished.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert rt.done is True

    @pytest.mark.asyncio
    async def test_failure_before_start_is_reported_on_the_response(self):
        errors: list = []

        class _Runtime:
            def __init__(self):
                self.hooks = _FakeHooks()

            async def invoke(self, text, options=None):
                raise RuntimeError("no model configured")

        await _start_prompt(_Runtime(), "hi", None, lambda: errors.append("ok"), errors.append)

        assert errors == ["no model configured"]

    @pytest.mark.asyncio
    async def test_runtime_without_hooks_stays_synchronous(self):
        calls: list = []

        class _Runtime:
            hooks = None

            async def invoke(self, text, options=None):
                calls.append(text)

        await _start_prompt(_Runtime(), "hi", None, lambda: calls.append("ok"), lambda e: None)
        assert calls == ["hi", "ok"]


class TestDialogTimeoutAndCancel:
    @pytest.mark.asyncio
    async def test_timeout_resolves_to_none_and_is_advertised(self, monkeypatch):
        sent: list = []
        monkeypatch.setattr(mode, "_write", sent.append)
        ctx = RpcExtensionUIContext({})

        result = await ctx.select("Pick", ["a"], timeout=0.01)

        assert result is None
        assert sent[0]["timeout"] == 10  # milliseconds
        assert ctx._pending == {}  # no leaked waiter

    @pytest.mark.asyncio
    async def test_cancel_pending_unblocks_waiting_dialogs(self, monkeypatch):
        monkeypatch.setattr(mode, "_write", lambda obj: None)
        ctx = RpcExtensionUIContext({})

        task = asyncio.ensure_future(ctx.select("Pick", ["a"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ctx._pending:
                break

        ctx.cancel_pending()
        assert await task is None

    @pytest.mark.asyncio
    async def test_confirm_maps_cancellation_to_false(self, monkeypatch):
        monkeypatch.setattr(mode, "_write", lambda obj: None)
        ctx = RpcExtensionUIContext({})

        task = asyncio.ensure_future(ctx.confirm("Sure?"))
        for _ in range(10):
            await asyncio.sleep(0)
            if ctx._pending:
                break

        ctx.cancel_pending()
        assert await task is False


class TestForwardedEvents:
    def test_transcript_critical_events_are_forwarded(self):
        # A client mirroring the session drifts without these.
        for name in (
            "message_rollback",
            "tool_execution_failure",
            "compaction_cancelled",
            "compaction_failure",
        ):
            assert name in mode._FORWARDED_EVENTS


class TestRpcExtensionUIContextIds:
    def test_ids_increment(self):
        ctx = RpcExtensionUIContext({})
        assert ctx._new_req_id() == "ui_1"
        assert ctx._new_req_id() == "ui_2"
        assert ctx._new_req_id() == "ui_3"

    def test_starts_at_zero(self):
        ctx = RpcExtensionUIContext({})
        assert ctx._next_id == 0


class TestRpcFireMethod:
    def test_fire_emits_without_awaiting(self):
        ctx = RpcExtensionUIContext({})
        _, lines = capture_write(ctx.notify, "Hello notification")
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["type"] == "extension_ui_request"
        assert obj["method"] == "notify"
        assert obj["message"] == "Hello notification"

    def test_fire_increments_id(self):
        ctx = RpcExtensionUIContext({})
        _, lines1 = capture_write(ctx.notify, "first")
        _, lines2 = capture_write(ctx.notify, "second")
        id1 = json.loads(lines1[0])["id"]
        id2 = json.loads(lines2[0])["id"]
        assert id1 != id2

    def test_set_status_fire(self):
        ctx = RpcExtensionUIContext({})
        _, lines = capture_write(ctx.set_status, "mykey", "Running...")
        obj = json.loads(lines[0])
        assert obj["method"] == "setStatus"
        assert obj["statusKey"] == "mykey"

    def test_set_widget_fire(self):
        ctx = RpcExtensionUIContext({})
        _, lines = capture_write(ctx.set_widget, "wkey", ["line1", "line2"])
        obj = json.loads(lines[0])
        assert obj["method"] == "setWidget"
        assert obj["widgetLines"] == ["line1", "line2"]

    def test_fire_does_not_add_to_pending(self):
        ctx = RpcExtensionUIContext({})
        capture_write(ctx.notify, "msg")
        assert len(ctx._pending) == 0


class TestRpcDialogMethod:
    def test_dialog_adds_future_to_pending_and_resolves(self):
        ctx = RpcExtensionUIContext({})
        captured_output = []

        async def _run():
            async def _dialog_task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.select("Pick one", ["a", "b"])
                finally:
                    sys.stdout = old
                    captured_output.append(buf.getvalue())

            task = asyncio.ensure_future(_dialog_task())
            # Poll until the pending future appears (coroutine needs ≥1 tick to
            # reach the `await fut` inside _dialog; a single sleep(0) is usually
            # enough but we loop for robustness against implementation changes)
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result("a")
                    break
            return await task

        result = asyncio.run(_run())
        assert result == "a"

    def test_confirm_truthy_dict(self):
        ctx = RpcExtensionUIContext({})

        async def _run():
            async def _confirm_task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.confirm("Are you sure?")
                finally:
                    sys.stdout = old

            task = asyncio.ensure_future(_confirm_task())
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result({"confirmed": True})
                    break
            return await task

        result = asyncio.run(_run())
        assert result is True

    def test_confirm_cancelled_dict(self):
        ctx = RpcExtensionUIContext({})

        async def _run():
            async def _confirm_task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.confirm("Are you sure?")
                finally:
                    sys.stdout = old

            task = asyncio.ensure_future(_confirm_task())
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result({"cancelled": True})
                    break
            return await task

        result = asyncio.run(_run())
        assert result is False


class TestRpcFireExtendedMethods:
    def test_set_title_fire(self):
        ctx = RpcExtensionUIContext({})
        _, lines = capture_write(ctx.set_title, "My Title")
        obj = json.loads(lines[0])
        assert obj["method"] == "setTitle"
        assert obj["title"] == "My Title"

    def test_set_editor_text_fire(self):
        ctx = RpcExtensionUIContext({})
        _, lines = capture_write(ctx.set_editor_text, "some prefill")
        obj = json.loads(lines[0])
        assert obj["method"] == "set_editor_text"
        assert obj["text"] == "some prefill"

    def test_set_title_does_not_add_to_pending(self):
        ctx = RpcExtensionUIContext({})
        capture_write(ctx.set_title, "x")
        assert len(ctx._pending) == 0

    def test_set_editor_text_does_not_add_to_pending(self):
        ctx = RpcExtensionUIContext({})
        capture_write(ctx.set_editor_text, "x")
        assert len(ctx._pending) == 0


class TestRpcInputEditorDialog:
    def test_input_dialog(self):
        ctx = RpcExtensionUIContext({})

        async def _run():
            async def _task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.input("Enter value", placeholder="e.g. foo")
                finally:
                    sys.stdout = old

            task = asyncio.ensure_future(_task())
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result("user typed this")
                    break
            return await task

        result = asyncio.run(_run())
        assert result == "user typed this"

    def test_input_emits_correct_method(self):
        ctx = RpcExtensionUIContext({})
        captured: list[dict] = []

        async def _run():
            async def _task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.input("Enter value")
                finally:
                    sys.stdout = old
                    lines = buf.getvalue().splitlines()
                    if lines:
                        captured.append(json.loads(lines[0]))

            task = asyncio.ensure_future(_task())
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result(None)
                    break
            return await task

        asyncio.run(_run())
        assert captured[0]["method"] == "input"

    def test_editor_dialog(self):
        ctx = RpcExtensionUIContext({})

        async def _run():
            async def _task():
                buf = StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    return await ctx.editor("Edit content", prefill="initial text")
                finally:
                    sys.stdout = old

            task = asyncio.ensure_future(_task())
            for _ in range(10):
                await asyncio.sleep(0)
                if ctx._pending:
                    break
            for _, fut in list(ctx._pending.items()):
                if not fut.done():
                    fut.set_result("edited content")
                    break
            return await task

        result = asyncio.run(_run())
        assert result == "edited content"
