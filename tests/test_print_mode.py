"""Print mode's run lifecycle (``tau/modes/print/mode.py``).

Both non-interactive shapes — ``-p`` (text) and ``--mode json`` (events) —
share the prompt loop and signal handling tested here; what reaches stdout is
covered by tests/test_json_mode_output.py and tests/test_wire.py.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import click
import pytest

from tau.hooks.engine import MessageEndEvent, SettledEvent
from tau.hooks.service import Hooks
from tau.message.types import AssistantMessage
from tau.modes.print import mode as print_mode


class _FakeRuntime:
    """Records prompts and settles each one, like a turn that produced text."""

    def __init__(self, reply: str = "done") -> None:
        self.hooks = Hooks()
        self.prompts: list[str] = []
        self.aborted = 0
        self._reply = reply
        self.agent = self

    def abort(self) -> None:
        self.aborted += 1

    async def invoke(self, message: str) -> None:
        self.prompts.append(message)
        await self.hooks.emit(MessageEndEvent(message=AssistantMessage.from_text(self._reply)))
        await self.hooks.emit(SettledEvent())


# ── Multiple prompts ─────────────────────────────────────────────────────────


class TestPromptSequence:
    def test_each_prompt_runs_in_order_against_one_session(self, capsys):
        runtime = _FakeRuntime()

        asyncio.run(print_mode.run_print_mode(runtime, ["first", "second", "third"]))  # type: ignore[arg-type]

        assert runtime.prompts == ["first", "second", "third"]

    def test_a_prompt_waits_for_the_previous_one_to_settle(self):
        """Firing them all at once would have the second land mid-turn, which
        the agent rejects as busy."""
        order: list[str] = []

        class _SlowRuntime(_FakeRuntime):
            async def invoke(self, message: str) -> None:
                order.append(f"start:{message}")
                await asyncio.sleep(0)
                order.append(f"end:{message}")
                await self.hooks.emit(SettledEvent())

        asyncio.run(print_mode.run_print_mode(_SlowRuntime(), ["a", "b"], output="json"))  # type: ignore[arg-type]

        assert order == ["start:a", "end:a", "start:b", "end:b"]

    def test_no_messages_is_a_clean_cli_error(self):
        with pytest.raises(click.ClickException, match="message is required"):
            asyncio.run(print_mode.run_print_mode(_FakeRuntime(), []))  # type: ignore[arg-type]


# ── Signals ──────────────────────────────────────────────────────────────────


class TestSignalHandling:
    """A single-shot run is usually driven by a script or CI step, so it gets
    killed rather than quit. Without handling, the agent keeps streaming after
    the shell has moved on and whatever its tools spawned is orphaned."""

    @staticmethod
    def _raise_on_signal(runtime, sig: signal.Signals):
        async def scenario() -> None:
            task = asyncio.ensure_future(
                print_mode.run_print_mode(runtime, ["prompt"], output="json")
            )
            await asyncio.sleep(0)
            signal.raise_signal(sig)
            await task

        asyncio.run(scenario())

    @pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="no SIGTERM")
    def test_sigterm_aborts_the_turn_and_exits_143(self):
        class _HangingRuntime(_FakeRuntime):
            async def invoke(self, message: str) -> None:
                self.prompts.append(message)
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if self.aborted:
                        break
                await self.hooks.emit(SettledEvent())

        runtime = _HangingRuntime()

        with pytest.raises(print_mode.Interrupted) as excinfo:
            self._raise_on_signal(runtime, signal.SIGTERM)

        assert runtime.aborted == 1
        assert excinfo.value.code == print_mode.EXIT_SIGTERM == 143

    @pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP")
    def test_sighup_exits_129(self):
        class _HangingRuntime(_FakeRuntime):
            async def invoke(self, message: str) -> None:
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if self.aborted:
                        break
                await self.hooks.emit(SettledEvent())

        with pytest.raises(print_mode.Interrupted) as excinfo:
            self._raise_on_signal(_HangingRuntime(), signal.SIGHUP)

        assert excinfo.value.code == print_mode.EXIT_SIGHUP == 129

    def test_handlers_are_removed_after_the_run(self):
        """They are installed on the shared loop, so leaving them behind would
        have a later run inherit an abort pointed at a dead runtime."""
        from tau.modes.signals import exit_on_signal

        async def scenario() -> None:
            loop = asyncio.get_event_loop()
            with exit_on_signal(lambda: None):
                pass
            # remove_signal_handler returns False when nothing was installed.
            assert loop.remove_signal_handler(signal.SIGTERM) is False

        asyncio.run(scenario())

    def test_an_uninterrupted_run_reports_nothing(self, capsys):
        runtime = _FakeRuntime()

        asyncio.run(print_mode.run_print_mode(runtime, ["prompt"]))

        assert runtime.aborted == 0
        assert capsys.readouterr().out == "done"


# ── Output selection ─────────────────────────────────────────────────────────


class TestOutputSelection:
    def test_text_prints_only_the_final_message(self, capsys):
        asyncio.run(print_mode.run_print_mode(_FakeRuntime("the answer"), ["p"]))  # type: ignore[arg-type]

        assert capsys.readouterr().out == "the answer"

    def test_json_emits_events_and_no_bare_text(self, capsys):
        asyncio.run(print_mode.run_print_mode(_FakeRuntime("the answer"), ["p"], output="json"))  # type: ignore[arg-type]

        out = capsys.readouterr().out
        assert '"type": "message_end"' in out
        assert not out.endswith("the answer")

    def test_an_error_on_the_final_message_is_a_cli_failure(self):
        class _FailingRuntime(_FakeRuntime):
            async def invoke(self, message: str) -> None:
                msg = AssistantMessage.from_text("")
                msg.error = "provider exploded"
                await self.hooks.emit(MessageEndEvent(message=msg))
                await self.hooks.emit(SettledEvent())

        with pytest.raises(click.ClickException, match="provider exploded"):
            asyncio.run(print_mode.run_print_mode(_FailingRuntime(), ["p"]))  # type: ignore[arg-type]


# ── CLI wiring ───────────────────────────────────────────────────────────────


class TestCliWiring:
    def test_print_mode_lives_under_modes_not_the_cli(self):
        """`tau/modes/print/` was an empty namespace package while both run
        modes sat in console/cli.py, contradicting the documented layout."""
        from tau.console import cli

        assert not hasattr(cli, "_run_print")
        assert not hasattr(cli, "_run_json")
        assert callable(print_mode.run_print_mode)

    def test_repeated_prompts_reach_the_mode_in_order(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", _NotATty())

        from tau.console.cli import _build_messages

        assert _build_messages(("one", "two", "three"), ()) == ["one", "two", "three"]

    def test_stdin_and_files_attach_to_the_first_prompt_only(self, tmp_path, monkeypatch):
        source = tmp_path / "a.py"
        source.write_text("print(1)\n")
        monkeypatch.setattr("sys.stdin", _NotATty())

        from tau.console.cli import _build_messages

        built = _build_messages(("review", "now summarise"), (source,))

        assert len(built) == 2
        assert "a.py" in built[0] and "review" in built[0]
        assert built[1] == "now summarise"


class _NotATty:
    @staticmethod
    def isatty() -> bool:
        return True

    @staticmethod
    def read() -> str:
        return ""


def _unused(_: Any) -> None:  # pragma: no cover - keeps linters quiet on Any import
    return None


class TestSharedSignalHandling:
    """rpc, print and json are all headless and all get killed rather than
    quit, so the signal-to-exit-code rule is written once and used by each."""

    def test_all_headless_modes_use_the_shared_helper(self):
        import inspect

        from tau.modes.rpc import mode as rpc_mode

        assert "exit_on_signal" in inspect.getsource(rpc_mode.run_rpc_mode)
        assert "exit_on_signal" in inspect.getsource(print_mode._run_text)
        assert "exit_on_signal" in inspect.getsource(print_mode._run_json)

    def test_the_cli_maps_the_shared_exception_to_an_exit_code(self):
        import inspect

        from tau.console import cli
        from tau.modes.signals import Interrupted

        source = inspect.getsource(cli._start)

        assert "except Interrupted" in source
        assert "SystemExit(exc.code)" in source
        assert Interrupted(143).code == 143

    def test_the_first_signal_wins(self):
        """A second signal during shutdown must not rewrite the reported cause."""
        from tau.modes.signals import EXIT_SIGHUP, EXIT_SIGTERM, exit_on_signal

        async def scenario() -> None:
            with exit_on_signal(lambda: None) as interrupted:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(0.05)
                signal.raise_signal(signal.SIGHUP)
                await asyncio.sleep(0.05)
            assert interrupted["code"] == EXIT_SIGTERM != EXIT_SIGHUP

        asyncio.run(scenario())

    def test_a_failing_callback_does_not_lose_the_signal(self):
        """The exit code matters even if aborting the agent blew up."""
        from tau.modes.signals import EXIT_SIGTERM, exit_on_signal

        def _boom() -> None:
            raise RuntimeError("abort failed")

        async def scenario() -> None:
            with exit_on_signal(_boom) as interrupted:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(0.05)
            assert interrupted["code"] == EXIT_SIGTERM

        asyncio.run(scenario())
