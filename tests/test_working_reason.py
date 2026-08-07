"""The spinner should say what is actually happening while a prompt is open.

The spinner is `tui.children[3]`; a selector renders inside `Layout`, the last
child. They are siblings, `Layout.render` never touches the spinner, and
nothing in the selector path stops it — so an approval prompt leaves it reading
"Tool Calling…" while nothing is calling a tool, with the elapsed timer
climbing for as long as the prompt sits unanswered.

`push_working_reason`/`pop_working_reason` layer a temporary label over that.
The layering is the point: `set_working_message(None)` reverts to the *default*
label rather than to whatever the turn was showing, so a gate using it would
leave "Thinking…" behind in the middle of a tool call.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tau.modes.interactive.ui_context import UIContext
from tau.tui.components.spinner import Spinner
from tau.tui.utils import strip_ansi


class _Tui:
    _render_requested = False

    def request_render(self) -> None: ...


class _Layout:
    def __init__(self) -> None:
        self.spinner = Spinner(_Tui())
        self._tui = _Tui()


def _ctx() -> tuple[UIContext, _Layout]:
    layout = _Layout()
    ctx = UIContext.__new__(UIContext)
    ctx._layout_ref = lambda: layout  # type: ignore[assignment]
    return ctx, layout


def _label(layout: _Layout) -> str:
    rendered = layout.spinner.render(80)
    return strip_ansi(rendered[0]) if rendered else ""


@pytest.fixture
def working():
    """A spinner mid-turn, as it is when a permission prompt opens."""
    ctx, layout = _ctx()
    layout.spinner.set_label("Tool Calling…")

    async def _start() -> None:
        layout.spinner.start_turn()

    asyncio.run(_start())
    yield ctx, layout
    layout.spinner.dispose()


def test_the_spinner_keeps_running_while_a_prompt_is_open(working) -> None:
    """It is a sibling of the picker; opening one cannot stop it."""
    _, layout = working

    assert layout.spinner.active is True
    assert "Tool Calling…" in _label(layout)


def test_pushing_a_reason_changes_the_label(working) -> None:
    ctx, layout = working

    ctx.push_working_reason("permissions", "Waiting for approval…")

    assert "Waiting for approval…" in _label(layout)
    assert "Tool Calling…" not in _label(layout)


def test_popping_restores_the_turns_own_label(working) -> None:
    """The whole reason for layering rather than set_working_message."""
    ctx, layout = working

    ctx.push_working_reason("permissions", "Waiting for approval…")
    ctx.pop_working_reason("permissions")

    assert "Tool Calling…" in _label(layout)


def test_set_working_message_would_not_have_restored_it(working) -> None:
    """Pins why the new API exists: None reverts to the default, not the turn's."""
    ctx, layout = working

    ctx.set_working_message("Waiting for approval…")
    ctx.set_working_message(None)

    assert "Thinking…" in _label(layout)
    assert "Tool Calling…" not in _label(layout)


def test_popping_a_key_that_was_never_pushed_is_safe(working) -> None:
    """It belongs in a finally, so it must tolerate never having run."""
    ctx, layout = working

    ctx.pop_working_reason("permissions")

    assert "Tool Calling…" in _label(layout)


def test_two_drivers_do_not_clobber_each_other(working) -> None:
    ctx, layout = working

    ctx.push_working_reason("permissions", "Waiting for approval…")
    ctx.push_working_reason("compaction", "Compacting…")
    ctx.pop_working_reason("compaction")

    assert "Waiting for approval…" in _label(layout)


def test_the_spinner_stays_visible_throughout(working) -> None:
    ctx, layout = working

    ctx.push_working_reason("permissions", "Waiting for approval…")

    assert layout.spinner.render(80), "the spinner rendered nothing"


def test_a_missing_layout_is_tolerated() -> None:
    """The UI can go away mid-prompt (session switch, shutdown)."""
    ctx = UIContext.__new__(UIContext)
    ctx._layout_ref = lambda: None  # type: ignore[assignment]

    ctx.push_working_reason("k", "v")
    ctx.pop_working_reason("k")


class TestRpcParity:
    """prompt.py calls these on whatever surface it has; RPC must not explode."""

    def _rpc(self) -> Any:
        from tau.modes.rpc.ui_context import RpcUIContext

        return RpcUIContext.__new__(RpcUIContext)

    def test_rpc_accepts_both_calls(self) -> None:
        rpc = self._rpc()

        assert rpc.push_working_reason("permissions", "Waiting…") is None
        assert rpc.pop_working_reason("permissions") is None


class TestGateUsage:
    """The extension pushes and, crucially, always pops."""

    def _prompt(self):
        import importlib

        from tests.ext_loader import load_extension

        return importlib.import_module(f"{load_extension('permissions').__name__}.prompt")

    class _UI:
        supports_components = True

        def __init__(self, answer: str | None, explode: bool = False) -> None:
            self.answer, self.explode = answer, explode
            self.pushed: list[tuple[str, str]] = []
            self.popped: list[str] = []

        async def select(self, title, options):
            if self.explode:
                raise RuntimeError("picker died")
            return options[0] if self.answer else None

        def push_working_reason(self, key: str, label: str) -> None:
            self.pushed.append((key, label))

        def pop_working_reason(self, key: str) -> None:
            self.popped.append(key)

    def _ask(self, ui):
        import importlib

        from tests.ext_loader import load_extension

        pkg = load_extension("permissions").__name__
        rules = importlib.import_module(f"{pkg}.rules")
        decision = rules.Decision(state="ask", surface="command", target="ls")
        return asyncio.run(
            self._prompt().ask(ui, decision, timeout_seconds=0, params={"cmd": "ls"})
        )

    def test_it_pushes_a_waiting_label(self) -> None:
        ui = self._UI("Allow")

        self._ask(ui)

        # The wording is an editorial choice and changes; what has to hold is
        # that *a* label is pushed, under the key the finally pops, and that it
        # is not left saying "Tool Calling…" while the picker waits on a human.
        assert ui.pushed, "the spinner still said 'Tool Calling…' throughout"
        key, label = ui.pushed[0]
        assert key == "permissions"
        assert label.strip()
        assert "tool calling" not in label.lower()

    def test_it_pops_on_a_normal_answer(self) -> None:
        ui = self._UI("Allow")

        self._ask(ui)

        assert ui.popped == ["permissions"]

    def test_it_pops_when_the_prompt_is_dismissed(self) -> None:
        ui = self._UI(None)

        self._ask(ui)

        assert ui.popped == ["permissions"]

    def test_it_pops_when_the_dialog_raises(self) -> None:
        """A stranded label would outlive the prompt for the rest of the turn."""
        ui = self._UI("Allow", explode=True)

        outcome, _ = self._ask(ui)

        assert outcome == "deny"
        assert ui.popped == ["permissions"]

    def test_a_surface_without_the_api_still_prompts(self) -> None:
        class _Old:
            supports_components = True

            async def select(self, title, options):
                return options[0]

        outcome, _ = self._ask(_Old())

        assert outcome == "allow_once"
