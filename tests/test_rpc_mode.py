"""Tests for tau/rpc/mode.py — _write, _serialize_event, RpcExtensionUIContext."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from io import StringIO

from tau.modes.rpc.mode import RpcExtensionUIContext, _serialize_event, _write


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
