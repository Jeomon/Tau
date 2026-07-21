"""Tests for tau/modes/rpc/ui_context.py — ``ctx.ui`` in RPC mode."""

from __future__ import annotations

import asyncio

import pytest

import tau.modes.rpc.mode as mode
from tau.modes.interactive.ui_context import UIContext
from tau.modes.rpc.mode import RpcExtensionUIContext
from tau.modes.rpc.ui_context import RpcUIContext


@pytest.fixture
def sent(monkeypatch):
    lines: list = []
    monkeypatch.setattr(mode, "_write", lines.append)
    return lines


@pytest.fixture
def ui():
    return RpcUIContext(RpcExtensionUIContext({}))


class TestCapabilityFlag:
    def test_rpc_context_cannot_render_components(self, ui):
        assert ui.supports_components is False

    def test_interactive_context_can(self):
        assert UIContext.supports_components is True

    @pytest.mark.asyncio
    async def test_component_methods_degrade_instead_of_raising(self, ui, sent):
        assert await ui.custom(lambda *a: None) is None
        assert await ui.custom_inline(lambda *a: None) is None
        assert sent == []  # nothing goes on the wire

    def test_overlay_returns_a_closable_handle(self, ui):
        handle = ui.show_overlay(object())
        handle.close()  # must not raise


class TestDialogsReachTheClient:
    @pytest.mark.asyncio
    async def test_select_emits_a_request_and_resolves(self, ui, sent):
        task = asyncio.ensure_future(ui.select("Pick", ["a", "b"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        assert sent[0]["method"] == "select"
        assert sent[0]["options"] == ["a", "b"]

        for fut in ui._bridge._pending.values():
            fut.set_result("b")
        assert await task == "b"

    @pytest.mark.asyncio
    async def test_prompt_maps_onto_the_input_method(self, ui, sent):
        task = asyncio.ensure_future(ui.prompt("Name?"))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        assert sent[0]["method"] == "input"
        for fut in ui._bridge._pending.values():
            fut.set_result("tau")
        assert await task == "tau"

    @pytest.mark.asyncio
    async def test_confirm_and_editor_are_wired(self, ui, sent):
        for coro in (ui.confirm("Sure?"), ui.editor("Edit", "seed")):
            task = asyncio.ensure_future(coro)
            for _ in range(10):
                await asyncio.sleep(0)
                if ui._bridge._pending:
                    break
            for fut in ui._bridge._pending.values():
                fut.set_result(None)
            await task

        assert [line["method"] for line in sent] == ["confirm", "editor"]


class TestFireAndForget:
    def test_notify_joins_line_lists(self, ui, sent):
        ui.notify(["one", "two"], "warning")
        assert sent[0]["method"] == "notify"
        assert sent[0]["message"] == "one\ntwo"
        assert sent[0]["notifyType"] == "warning"

    def test_status_set_and_clear(self, ui, sent):
        ui.set_status("k", "busy")
        ui.clear_status("k")
        assert sent[0]["statusText"] == "busy"
        assert sent[1]["statusText"] is None

    def test_line_widgets_cross_the_protocol(self, ui, sent):
        ui.set_widget("w", ["line one"], placement="below_editor")
        assert sent[0]["method"] == "setWidget"
        assert sent[0]["widgetLines"] == ["line one"]
        assert sent[0]["widgetPlacement"] == "belowEditor"

    def test_component_widgets_are_dropped_not_sent(self, ui, sent):
        ui.set_widget("w", object())
        assert sent == []

    def test_remove_widget_clears_it(self, ui, sent):
        ui.remove_widget("w")
        assert sent[0]["widgetLines"] is None

    def test_title_and_editor_text(self, ui, sent):
        ui.set_title("t")
        ui.set_editor_text("hello")
        ui.paste_to_editor("world")
        assert [line["method"] for line in sent] == [
            "setTitle",
            "set_editor_text",
            "set_editor_text",
        ]


class TestNoOpSurface:
    def test_tui_only_methods_are_harmless(self, ui, sent):
        ui.set_footer(object())
        ui.restore_footer()
        ui.set_header(object())
        ui.set_editor_component(None)
        ui.clear_messages()
        ui.set_working_message("x")
        ui.set_working_visible(True)
        ui.set_working_indicator(["a"])
        ui.set_hidden_thinking_label("x")
        ui.request_render()
        ui.set_tools_expanded(True)
        ui.set_tool_results_expanded(True)

        assert sent == []  # none of this is representable on the wire

    def test_readers_return_neutral_values(self, ui):
        assert ui.get_editor_text() == ""
        assert ui.get_editor_component() is None
        assert ui.has_active_selector() is False
        assert ui.theme is None
        assert ui.get_all_themes() == []
        assert ui.set_theme("dark") is False
        assert ui.get_tools_expanded() is False

    def test_terminal_input_subscription_is_a_no_op(self, ui):
        unsub = ui.on_terminal_input(lambda event: True)
        unsub()  # must not raise


class TestExtensionContextWiring:
    def _context(self, bridge):
        from types import SimpleNamespace

        from tau.extensions.context import ExtensionContext

        runtime = SimpleNamespace(extension_ui_bridge=bridge, extension_generation=0)
        ctx = ExtensionContext(
            cwd=__import__("pathlib").Path("."),
            settings=None,
            model_id="",
            provider_id="",
            runtime=runtime,  # type: ignore[arg-type]
        )
        return ctx

    def test_ui_is_the_rpc_context_when_a_bridge_is_installed(self):
        ctx = self._context(RpcExtensionUIContext({}))
        assert isinstance(ctx.ui, RpcUIContext)
        assert ctx.has_ui is True

    def test_ui_is_none_with_no_layout_and_no_bridge(self):
        ctx = self._context(None)
        assert ctx.ui is None
        assert ctx.has_ui is False


class TestHasUiIsAnAlias:
    """`has_ui` must not drift from `ui is not None` — they are one predicate."""

    def _ctx(self, layout, bridge):
        import pathlib
        from types import SimpleNamespace

        from tau.extensions.context import ExtensionContext

        runtime = SimpleNamespace(extension_ui_bridge=bridge, extension_generation=0)
        return ExtensionContext(
            cwd=pathlib.Path("."),
            settings=None,
            model_id="",
            provider_id="",
            layout=layout,
            runtime=runtime,  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize(
        ("has_layout", "has_bridge", "expected"),
        [
            (False, False, False),  # print / JSON mode
            (False, True, True),  # RPC
            (True, False, True),  # TUI
            (True, True, True),  # TUI wins over the bridge
        ],
    )
    def test_they_always_agree(self, has_layout, has_bridge, expected):
        class _Layout:  # weakref-able stand-in; UIContext only holds a ref
            pass

        ctx = self._ctx(
            _Layout() if has_layout else None,
            RpcExtensionUIContext({}) if has_bridge else None,
        )

        assert ctx.has_ui is expected
        assert (ctx.ui is not None) is ctx.has_ui

    def test_a_layout_wins_over_the_bridge(self):
        class _Layout:
            pass

        ctx = self._ctx(_Layout(), RpcExtensionUIContext({}))
        assert isinstance(ctx.ui, UIContext)  # not the RPC stand-in
        assert ctx.ui.supports_components is True


class TestMultiSelect:
    @pytest.mark.asyncio
    async def test_returns_every_chosen_label(self, ui, sent):
        task = asyncio.ensure_future(ui.multi_select("Which?", ["a", "b", "c"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        assert sent[0]["method"] == "multi_select"
        assert sent[0]["options"] == ["a", "b", "c"]

        for fut in ui._bridge._pending.values():
            fut.set_result(["a", "c"])
        assert await task == ["a", "c"]

    @pytest.mark.asyncio
    async def test_empty_list_is_an_answer_not_a_cancel(self, ui):
        task = asyncio.ensure_future(ui.multi_select("Which?", ["a"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        for fut in ui._bridge._pending.values():
            fut.set_result([])
        assert await task == []  # distinct from None

    @pytest.mark.asyncio
    async def test_cancellation_is_none(self, ui):
        task = asyncio.ensure_future(ui.multi_select("Which?", ["a"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        ui._bridge.cancel_pending()
        assert await task is None

    @pytest.mark.asyncio
    async def test_a_bare_label_reply_is_wrapped(self, ui):
        task = asyncio.ensure_future(ui.multi_select("Which?", ["a"]))
        for _ in range(10):
            await asyncio.sleep(0)
            if ui._bridge._pending:
                break

        for fut in ui._bridge._pending.values():
            fut.set_result("a")  # client answered like a single select
        assert await task == ["a"]


class TestDialogSurfaceParity:
    """Neither context may grow a dialog the other lacks — see multi_select."""

    DIALOGS = ("select", "multi_select", "confirm", "prompt", "editor")

    @pytest.mark.parametrize("name", DIALOGS)
    def test_both_contexts_expose_the_same_dialogs(self, name):
        assert callable(getattr(RpcUIContext, name, None)), f"RpcUIContext lacks {name}"
        assert callable(getattr(UIContext, name, None)), f"UIContext lacks {name}"
